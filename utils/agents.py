from config import SUMMARY_RECOMMENDATION_PROMPT, GENERATE_SUMMARY_PROMPT
import requests


class BaseAIAgent:
    def __init__(self, api_key, model, prompt_template="", last_n_message=None, max_tokens=1024, temperature=0.7,
                 endpoint: str = None):
        """
        Base constructor for AI agents with common initialization parameters.

        :param api_key: Authentication key for the AI service
        :param model: Name of the AI model to use
        :param prompt_template: Optional template for prompts
        :param last_n_message: Recent conversation messages
        :param max_tokens: Maximum number of tokens in response
        :param temperature: Creativity/randomness of the response
        :param endpoint: API endpoint URL
        """
        self.api_key = api_key
        self.prompt_template = prompt_template
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.endpoint = endpoint
        self.model = model
        self.last_n_message = last_n_message

    def create_summary_prompt(self) -> str:
        """
        Create a standard summary prompt for conversations.

        :return: Formatted prompt for conversation summary
        """
        # Ensure the input is valid
        if not self.last_n_message:
            return "No conversations to summarize."

        # Format the conversations into a readable structure
        conversation_text = "\n".join(
            f"{conv.role}: {conv.message}" for conv in self.last_n_message
        )

        prompt = f"""
        Here are the most recent conversations:

        {conversation_text}
        """
        if self.prompt_template:
            prompt = prompt + "\n" + self.prompt_template
        else:
            prompt = prompt + "\n" + SUMMARY_RECOMMENDATION_PROMPT
        return prompt

    def generate_summary_prompt(self, metrics: dict)-> str:
        """
        Create a standard summary prompt for conversations.

        :return: Formatted prompt for conversation summary
        """
        # Ensure the input is valid
        if not self.last_n_message:
            return "No conversations to summarize."

        # Format the conversations into a readable structure
        conversation_text = "\n".join(
            f"{conv.role}: {conv.message}" for conv in self.last_n_message
        )

        prompt = f"""
                Here are the most recent conversations:
                {conversation_text}
                """
        metrics_text = f"Metrics:\n{metrics.__str__()}"

        FORMAT = """
        Format the response in HTML markdown using the following structure:
        <div class="analysis-container">
        </div>
        Important: Provide ONLY the HTML content without any markdown code fences or additional formatting. Start directly with <div> and end with </div>
        """

        if self.prompt_template:
            prompt = "\n".join([self.prompt_template, FORMAT, metrics_text, prompt])
        else:
            prompt = "\n".join([GENERATE_SUMMARY_PROMPT, FORMAT, metrics_text, prompt])
        return prompt

    def summarize_and_recommend(self, content) -> str:
        """
        Abstract method to be implemented by child classes.

        :param content: Content to summarize
        :return: Summarized content
        """
        raise NotImplementedError("Subclasses must implement abstract method")


class OpenAIAgent(BaseAIAgent):
    def summarize_and_recommend(self, content) -> str:
        """
        OpenAI-specific implementation of summarization.

        :param content: Content to summarize
        :return: Summarized content
        """
        url = f"{self.endpoint}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        messages = [
            {"role": "user", "content": content}
        ]

        data = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        response = requests.post(url, headers=headers, json=data)

        if response.status_code == 200:
            summary = response.json()["choices"][0]["message"]["content"]
            return summary
        else:
            print(f"Error: {response.status_code} - {response.text}")
            return None


class GoogleAIAgent(BaseAIAgent):
    def summarize_and_recommend(self, content) -> str:
        """
        Google AI-specific implementation of summarization.

        :param content: Content to summarize
        :return: Summarized content
        """
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        url = f"{self.endpoint}/{self.model}:generateContent"

        data = {
            "model": self.model,
            "contents": {'role': "user", 'parts': [{"text": content}]},
        }

        response = requests.post(url, headers=headers, json=data)

        if response.status_code == 200:
            candidate = response.json()['candidates'][0]
            summary = candidate['content']['parts'][0]['text']
            return summary
        else:
            print(f"Error: {response.status_code} - {response.text}")
            return None


class ClaudeAIAgent(BaseAIAgent):
    def summarize_and_recommend(self, content) -> str:
        """
        Claude AI-specific implementation of summarization.

        :param content: Content to summarize
        :return: Summarized content
        """
        url = f"{self.endpoint}/chat/completions"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }

        messages = [
            {"role": "user", "content": content}
        ]

        data = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        response = requests.post(url, headers=headers, json=data)

        if response.status_code == 200:
            summary = response.json()["choices"][0]["message"]["content"]
            return summary
        else:
            print(f"Error: {response.status_code} - {response.text}")
            return None
