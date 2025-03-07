from chatlib.chatlib.utils import dict_utils
from chatlib.chatlib.chatbot import ResponseGenerator, Dialogue, dialogue_utils
from chatlib.chatlib.chatbot.generators import ChatGPTResponseGenerator, StateBasedResponseGenerator, StateType
from chatlib.chatlib.chatbot.dialogue_to_csv import DialogueCSVWriter, TurnValueExtractor
from chatlib.chatlib.chatbot.message_transformer import SpecialTokenExtractionTransformer

from app.common import EmotionChatbotPhase, SPECIAL_TOKEN_REGEX, SPECIAL_TOKEN_CONFIG, ChatbotLocale, FindDialogueSummarizerParams
import app.common
from app.phases import explore, label, find, record, share, help


class EmotionChatbotResponseGenerator(StateBasedResponseGenerator[EmotionChatbotPhase]):

    def __init__(self,
                 user_name: str | None = None,
                 user_age: int = None,
                 locale: ChatbotLocale = ChatbotLocale.Korean,
                 verbose: bool = False):
        super().__init__(initial_state=EmotionChatbotPhase.Explore,
                         verbose=verbose,
                         message_transformers=[
                             SpecialTokenExtractionTransformer.remove_all_regex("clean_special_tokens",
                                                                                SPECIAL_TOKEN_REGEX)
                         ]
                         )

        self.__user_name = user_name
        self.__user_age = user_age
        self.__locale = locale

        self.__generators: dict[EmotionChatbotPhase, ChatGPTResponseGenerator] = dict()

        self.__generators[EmotionChatbotPhase.Explore] = explore.create_generator()
        self.__generators[EmotionChatbotPhase.Label] = label.create_generator()
        self.__generators[EmotionChatbotPhase.Find] = find.create_generator()
        self.__generators[EmotionChatbotPhase.Record] = record.create_generator()
        self.__generators[EmotionChatbotPhase.Share] = share.create_generator()
        self.__generators[EmotionChatbotPhase.Help] = help.create_generator()

    def write_to_json(self, parcel: dict):
        super().write_to_json(parcel)
        parcel["user_name"] = self.__user_name
        parcel["user_age"] = self.__user_age
        parcel["locale"] = self.__locale

    def restore_from_json(self, parcel: dict):
        self.__user_name = parcel["user_name"]
        self.__user_age = parcel["user_age"]
        self.__locale = parcel["locale"] if "locale" in parcel else ChatbotLocale.Korean

        super().restore_from_json(parcel)

    @property
    def user_name(self)->str:
        return self.__user_name

    @property
    def user_age(self)->int:
        return self.__user_age

    @property
    def locale(self)->ChatbotLocale:
        return self.__locale

    @locale.setter
    def locale(self, locale: ChatbotLocale):
        self.__locale = locale
        self.__generators[self.current_state].update_instruction_parameters(dict(locale=locale))

    def get_generator(self, state: StateType, payload: dict | None) -> ResponseGenerator:
        # Get generator caches
        generator = self.__generators[state]

        if state == EmotionChatbotPhase.Explore:
            generator.update_instruction_parameters(
                dict(user_name=self.__user_name, 
                     user_age=self.__user_age,
                     locale=self.__locale,
                     evisited=True if payload is not None and payload[
                                                             "revisited"] is True else False))
        elif state == EmotionChatbotPhase.Label:
            generator.update_instruction_parameters(dict(**payload, locale=self.__locale))  # Put the result of rapport conversation
        elif state in [EmotionChatbotPhase.Find, EmotionChatbotPhase.Share,
                      EmotionChatbotPhase.Record]:
            generator.update_instruction_parameters(
                dict(key_episode=self._get_memoized_payload(EmotionChatbotPhase.Explore)["key_episode"],
                     identified_emotions=self._get_memoized_payload(EmotionChatbotPhase.Label)["identified_emotions"],
                     locale=self.__locale
                     )
            )
        return generator

    def update_generator(self, generator: ResponseGenerator, payload: dict | None):
        if isinstance(generator, ChatGPTResponseGenerator) and payload is not None:
            generator.update_instruction_parameters(dict(summarizer_result=payload))

    async def calc_next_state_info(self, current: EmotionChatbotPhase, dialog: Dialogue) -> tuple[
                                                                                                EmotionChatbotPhase | None, dict | None] | None:

        # dialog = dialogue_utils.extract_last_turn_sequence(dialog, lambda turn: dict_utils.get_nested_value(turn.metadata, "state") == current or turn.is_user)

        current_state_ai_turns = [turn for turn in StateBasedResponseGenerator.trim_dialogue_recent_n_states(dialog, 1)
                                  if
                                  turn.is_user == False]

        if len(dialog) == 0:
            return None
        # Check if the user expressed sensitive topics
        summarizer_result = await help.summarizer.run(None, dialog, help.summarizer_params)
        if summarizer_result.sensitive_topic is True:
            return EmotionChatbotPhase.Help, None

        # Explore --> Label
        if current == EmotionChatbotPhase.Explore:
            # Minimum 3 rapport building conversation turns
            if len(current_state_ai_turns) >= 2:
                summarizer_result = await explore.summarizer.run(explore.summarizer_examples, dialog, explore.summarizer_params)
                print(summarizer_result)
                # print(f"Phase suggestion: {phase_suggestion}")
                if summarizer_result.move_to_next is True:
                    return EmotionChatbotPhase.Label, summarizer_result.model_dump()
                else:
                    return None, summarizer_result.model_dump()
        # Label --> Find OR Record
        elif current == EmotionChatbotPhase.Label:
            print("Current AI turns: ", len(current_state_ai_turns))
            summarizer_result = \
                await label.summarizer.run(label.summarizer_examples, 
                                            dialog, app.common.LabelDialogueSummarizerParams(
                                            key_episode=self._get_memoized_payload(
                                            EmotionChatbotPhase.Explore)[
                                                                   "key_episode"],
                                                               user_emotion=self._get_memoized_payload(
                                                                   EmotionChatbotPhase.Explore)[
                                                                   "user_emotion"],
                                                           ))
            print(summarizer_result)

            if summarizer_result.next_phase == "find":
                if len(current_state_ai_turns) >= 3:
                    return EmotionChatbotPhase.Find, summarizer_result.model_dump()
            elif summarizer_result.next_phase == "record":
                if len(current_state_ai_turns) >= 3:
                    return EmotionChatbotPhase.Record, summarizer_result.model_dump()
            else:
                return None, summarizer_result.model_dump()
        # Find/Record --> Share
        elif current == EmotionChatbotPhase.Find or current == EmotionChatbotPhase.Record:

            summarizer = find.summarizer if current == EmotionChatbotPhase.Find else record.summarizer
            summarizer_result = await summarizer.run(find.summarizer_examples if current == EmotionChatbotPhase.Find else record.summarizer_examples, dialog,
                                                     FindDialogueSummarizerParams(
                                                             key_episode=
                                                             self._get_memoized_payload(
                                                                 EmotionChatbotPhase.Explore)[
                                                                 "key_episode"],
                                                             identified_emotions=self._get_memoized_payload(
                                                                 EmotionChatbotPhase.Label)[
                                                                 "identified_emotions"]))
            print(summarizer_result)
            if summarizer_result.proceed_to_next_phase is True and len(
                    current_state_ai_turns) >= 2:
                return EmotionChatbotPhase.Share, summarizer_result.model_dump()
            else:
                return None, summarizer_result.model_dump()
        # Share --> Explore or Terminate
        elif current == EmotionChatbotPhase.Share:
            last_turn_with_flag, last_turn_with_flag_index = dialogue_utils.find_last_turn(dialog, lambda turn: dict_utils.get_nested_value(turn.metadata, "new_episode_requested") == True)
            if last_turn_with_flag_index != -1 and last_turn_with_flag_index < len(dialog)-1:
                # if the flagged turn exists and is not the last one...
                result = await share.summarizer.run(None, dialog[last_turn_with_flag_index:], FindDialogueSummarizerParams(
                                                             key_episode=
                                                             self._get_memoized_payload(
                                                                 EmotionChatbotPhase.Explore)[
                                                                 "key_episode"],
                                                             identified_emotions=self._get_memoized_payload(
                                                                 EmotionChatbotPhase.Label)[
                                                                 "identified_emotions"]))
                if result.share_new_episode:
                    return EmotionChatbotPhase.Explore, {"revisited": True}
        return None

    async def _get_response_impl(self, dialog: Dialogue, dry: bool = False) -> tuple[str, dict | None]:
        msg, metadata = await super()._get_response_impl(dialog, dry)
        return msg, dict_utils.set_nested_value(metadata, "locale", self.locale)

    @staticmethod
    def get_csv_writer(session_id: str)->DialogueCSVWriter:
        return DialogueCSVWriter(
            columns=["state", *[key for token, key, _ in SPECIAL_TOKEN_CONFIG], "model", "prompt_tokens", "message_tokens"],
            column_extractors=[
                TurnValueExtractor(["metadata", "state"]),
                *[TurnValueExtractor(["metadata", key]) for token, key, value in SPECIAL_TOKEN_CONFIG],
                TurnValueExtractor(["metadata", "chatgpt", "model"]),
                TurnValueExtractor(["metadata", "chatgpt", "usage", "prompt_tokens"]),
                TurnValueExtractor(["metadata", "chatgpt", "usage", "completion_tokens"])
            ]
        ).insertColumn("session", lambda turn, index, params: session_id, 0)