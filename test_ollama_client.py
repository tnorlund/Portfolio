"""Test script to verify Ollama client setup."""

import asyncio
import logging
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def test_ollama_client():
    """Test that the Ollama client is working."""
    
    # Create the client exactly as in dev.add_metadatas.py
    client = AsyncOpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
    )
    
    try:
        # Test 1: Basic client test
        logger.info("Testing basic Ollama client...")
        response = await client.chat.completions.create(
            model="gpt-oss:20b",  # or whatever model you have
            messages=[
                {"role": "user", "content": "Say 'Hello, I am working!'"}
            ],
            max_tokens=50
        )
        logger.info(f"Basic test response: {response.choices[0].message.content}")
        
        # Test 2: OpenAIChatCompletionsModel wrapper
        logger.info("Testing OpenAIChatCompletionsModel wrapper...")
        model = OpenAIChatCompletionsModel(
            model="gpt-oss:20b",
            openai_client=client
        )
        
        # Try to get a simple response
        from agents import Agent, Runner
        agent = Agent(
            name="TestAgent",
            instructions="You are a test agent. Just respond to the user.",
            model=model,
            tools=[]
        )
        
        result = await Runner.run(
            agent,
            [{"role": "user", "content": "Say 'Agent is working!'"}]
        )
        logger.info(f"Agent test result: {result}")
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        return False
    
    return True

if __name__ == "__main__":
    success = asyncio.run(test_ollama_client())
    if success:
        print("\n✅ Ollama client is working correctly!")
    else:
        print("\n❌ Ollama client test failed. Check the logs above.")