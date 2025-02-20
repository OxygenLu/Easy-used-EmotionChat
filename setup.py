from os import path, getcwd

from chatlib.chatlib.global_config import GlobalConfig
from chatlib.chatlib.llm.integration.openai_api import GPTChatCompletionAPI
from dotenv import find_dotenv, set_key

#Create env files if not exist ========================================================

if __name__ == "__main__":
    GlobalConfig.is_cli_mode = True
    GPTChatCompletionAPI().assert_authorize()

print("Wrote .env file.")