"""
Ollama Turbo (Hosted) Client for LangChain
==========================================

This module provides a properly authenticated ChatOllama subclass that works
with Ollama's hosted API (Turbo) by correctly passing authentication headers.
"""

from typing import Any, Dict, List, Optional
import ollama
from langchain_ollama import ChatOllama
from langchain_core.messages import BaseMessage


class ChatOllamaTurbo(ChatOllama):
    """
    ChatOllama subclass that properly handles authentication for Ollama Turbo (hosted API).
    
    This fixes the authentication issue where langchain-ollama doesn't properly
    pass API keys to the hosted Ollama API.
    """
    
    def __init__(
        self, 
        model: str = "gpt-oss:20b",
        base_url: str = "https://ollama.com",
        api_key: Optional[str] = None,
        **kwargs
    ):
        # Initialize parent without the api_key (since it doesn't handle it properly)
        super().__init__(model=model, base_url=base_url, **kwargs)
        
        # Create our own authenticated ollama clients (sync and async)
        if api_key:
            # Create authenticated sync client
            self._authenticated_client = ollama.Client(
                host=base_url,
                headers={'Authorization': f'Bearer {api_key}'}
            )
            # Create authenticated async client
            self._authenticated_async_client = ollama.AsyncClient(
                host=base_url,
                headers={'Authorization': f'Bearer {api_key}'}
            )
        else:
            raise ValueError("API key is required for Ollama Turbo")
    
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager = None,
        **kwargs: Any,
    ) -> Any:
        """Override to use our authenticated client."""
        
        # Convert LangChain messages to Ollama format
        ollama_messages = []
        for msg in messages:
            role = msg.type
            if role == 'human':
                role = 'user'
            elif role == 'ai':
                role = 'assistant'
                
            ollama_messages.append({
                'role': role,
                'content': msg.content
            })
        
        # Use our authenticated client
        try:
            response = self._authenticated_client.chat(
                model=self.model,
                messages=ollama_messages,
                stream=False,
                options=kwargs.get('options', {})
            )
            
            # Convert response back to LangChain format
            from langchain_core.messages import AIMessage
            from langchain_core.outputs import ChatGeneration, ChatResult
            
            ai_message = AIMessage(content=response['message']['content'])
            generation = ChatGeneration(message=ai_message)
            
            return ChatResult(generations=[generation])
            
        except Exception as e:
            # If our client fails, fall back to parent implementation
            # (though it will likely fail with auth issues)
            return super()._generate(messages, stop, run_manager, **kwargs)
    
    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager = None,
        **kwargs: Any,
    ) -> Any:
        """Async override to use our authenticated client."""
        
        # Convert LangChain messages to Ollama format
        ollama_messages = []
        for msg in messages:
            role = msg.type
            if role == 'human':
                role = 'user'
            elif role == 'ai':
                role = 'assistant'
                
            ollama_messages.append({
                'role': role,
                'content': msg.content
            })
        
        # Use our authenticated async client
        try:
            # Use async chat method
            response = await self._authenticated_async_client.chat(
                model=self.model,
                messages=ollama_messages,
                stream=False,
                options=kwargs.get('options', {})
            )
            
            # Convert response back to LangChain format
            from langchain_core.messages import AIMessage
            from langchain_core.outputs import ChatGeneration, ChatResult
            
            ai_message = AIMessage(content=response['message']['content'])
            generation = ChatGeneration(message=ai_message)
            
            return ChatResult(generations=[generation])
            
        except Exception as e:
            # If our client fails, fall back to parent implementation
            # (though it will likely fail with auth issues)
            return await super()._agenerate(messages, stop, run_manager, **kwargs)


def create_ollama_turbo_client(
    model: str = "gpt-oss:20b",
    base_url: str = "https://ollama.com", 
    api_key: Optional[str] = None,
    temperature: float = 0.0
) -> ChatOllamaTurbo:
    """
    Create an authenticated Ollama Turbo client.
    
    Args:
        model: The model name (e.g., "gpt-oss:20b")
        base_url: The Ollama Turbo base URL
        api_key: The API key for authentication
        temperature: Temperature for response generation
        
    Returns:
        Authenticated ChatOllamaTurbo client
    """
    return ChatOllamaTurbo(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature
    )


if __name__ == "__main__":
    # Test the client
    import os
    from dotenv import load_dotenv
    from langchain_core.messages import HumanMessage
    
    load_dotenv()
    
    try:
        client = create_ollama_turbo_client(
            api_key=os.getenv("OLLAMA_API_KEY")
        )
        
        messages = [HumanMessage(content="Hello, this is a test")]
        response = client.invoke(messages)
        
        print("✅ Ollama Turbo client working!")
        print(f"Response: {response.content}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()