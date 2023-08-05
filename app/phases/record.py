from chatlib.chatbot import RegenerateRequestException
from chatlib.chatbot.generators import ChatGPTResponseGenerator, StateBasedResponseGenerator
from chatlib.jinja_utils import convert_to_jinja_template
from chatlib.mapper import ChatGPTDialogueSummarizer, ChatGPTDialogSummarizerParams
from chatlib.openai_utils import ChatGPTModel

from app.common import PromptFactory


# Encourage the user to record the moments in which they felt positive emotions.
def create_generator():
    return ChatGPTResponseGenerator(
        base_instruction=convert_to_jinja_template(PromptFactory.GENERATOR_PROMPT_BLOCK_KEY_EPISODE_AND_EMOTION_TYPES + """
        
- The goal of the current conversation is to encourage the user to keep diary to record the moments in which they felt positive emotions:
{%- for em in identified_emotions | selectattr("is_positive", "true") %}
  * {{em.emotion}} (em.reason)
{%- endfor %}

- 1. First start with asking the user whether they have been keeping diaries or journals regularly.
- 2. Then encourage the user to keep diary to record the moments in which they felt positive emotions.
- 3. Suggest a diary content by explicitly providing an example essay summarizing the above positive emotions and the reason; 
  for the essay, use '~다' style Korean, such as "~했다." rather than "~했어."; 
  put the diary content wrapped with <diary></diary>, at the end of the message;
  use the phrase like "예를 들어 다음과 같은 내용으로 일기를 써볼 수 있을 거야."
  
- Since the user is currently conversing with you, don't ask them to record now.""" + """
{% if summarizer_result != Undefined -%}

[Guide to the conversation]
{% if summarizer_result.asked_user_keeping_diary is false -%}
- You still did not ask whether the user is keeping diary these days. Ask about it.
{%- elif summarizer_result.explained_importance_of_recording is false %}
- You still did not explain the importance of recording the emotions. Explain it.
{%- elif summarizer_result.reflection_note_content_provided is false %}
- You still did not provide the example diary content. Provide it.
{%- endif %}
{%- endif %}

""" + PromptFactory.get_speaking_rules_block()),
    )


class RecordPhaseDialogSummarizer(ChatGPTDialogueSummarizer):
    def __init__(self):
        super().__init__(base_instruction=convert_to_jinja_template("""
- You are a helpful assistant that analyzes the content of the dialogue history.
""" +
                                                                    PromptFactory.SUMMARIZER_PROMPT_BLOCK_KEY_EPISODE_AND_EMOTION_TYPES + """
- The AI in the dialogue is encouraging the user to record the moments in which they felt positive emotions: {{ identified_emotions | selectattr("is_positive", "true") | map(attribute="emotion") | join(", ") }}.

- Analyze the input dialogue and identify if the AI had sufficient conversation about the recording.
Follow this JSON format: {
    "asked_user_keeping_diary": boolean // true if the AI had asked whether the user is keeping diary at present.
    "explained_importance_of_recording": boolean // true if the AI had described the importance of recording positive moments.
    "reflection_note_content_provided": boolean // Whether the AI has provided the reflection note to the user with <diary> tag.
}.
"""), model=ChatGPTModel.GPT_3_5_latest,
                         dialogue_filter=lambda dialogue, _: StateBasedResponseGenerator.trim_dialogue_recent_n_states(
                             dialogue, 3)
                         )

    def _postprocess_chatgpt_output(self, output: str, params: ChatGPTDialogSummarizerParams | None = None) -> dict:
        result = super()._postprocess_chatgpt_output(output, params)
        try:
            result["proceed_to_next_phase"] = result["asked_user_keeping_diary"] == True and result["explained_importance_of_recording"] == True and result["reflection_note_content_provided"] == True
            return result
        except:
            raise RegenerateRequestException("Malformed data.")

summarizer = RecordPhaseDialogSummarizer()
