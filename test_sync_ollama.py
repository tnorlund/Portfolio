"""Test synchronous Ollama client."""

import logging
from openai import OpenAI
from agents import OpenAIChatCompletionsModel, Agent, Runner

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_sync_ollama():
    """Test synchronous Ollama client."""
    
    # Create synchronous client
    client = OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
    )
    
    try:
        # Test 1: Basic client test
        logger.info("Testing synchronous Ollama client...")
        response = client.chat.completions.create(
            model="gpt-oss:20b",
            messages=[
                {"role": "user", "content": "Say 'Hello, sync working!'"}
            ],
            max_tokens=50
        )
        logger.info(f"Basic test response: {response.choices[0].message.content}")
        
        # Test 2: With agents library
        logger.info("Testing with agents library...")
        model = OpenAIChatCompletionsModel(
            model="gpt-oss:20b",
            openai_client=client
        )
        
        agent = Agent(
            name="TestAgent",
            instructions="You are a test agent. Just respond to the user.",
            model=model,
            tools=[]
        )
        
        result = Runner.run_sync(
            agent,
            [{"role": "user", "content": "Say 'Sync agent works!'"}]
        )
        logger.info(f"Agent result: {result.new_items[-1].output if result.new_items else 'No output'}")
        
        return True
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        return False

if __name__ == "__main__":
    if test_sync_ollama():
        print("\n✅ Synchronous Ollama client works!")
    else:
        print("\n❌ Synchronous test failed.")