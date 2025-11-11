"""Search Google Places API for merchant using LangChain with tools and manual tool calling."""

import traceback
from typing import Dict, Any, List
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from receipt_label.langchain.state.metadata_creation import MetadataCreationState
from receipt_label.langchain.tools.places_api_tools import create_places_api_tools
from langchain_core.tools import tool


async def search_places_for_merchant_with_agent(
    state: MetadataCreationState,
    google_places_api_key: str,
    ollama_api_key: str,
    thinking_strength: str = "medium",
) -> Dict[str, Any]:
    """Search Google Places API for merchant using an agent with tools.

    This uses AgentExecutor to intelligently decide which search methods to use
    and in what order, based on the extracted merchant information.

    The tools automatically use DynamoDB caching via PlacesAPI, so repeated
    searches for the same phone/address will be fast and cost-effective.

    Args:
        state: Current workflow state
        google_places_api_key: Google Places API key
        ollama_api_key: Ollama Cloud API key (required)
        thinking_strength: Ollama thinking strength - "low", "medium", or "high" (default: "medium")

    Returns:
        Dictionary with places search results and selected place
    """
    print(f"🔎 Searching Google Places for merchant using intelligent agent...")

    # Create tools (these automatically use DynamoDB caching via PlacesAPI)
    places_tools = create_places_api_tools(google_places_api_key)

    # Add a tool for the agent to explicitly select the best result
    selected_place_result: Dict[str, Any] = {"place_id": None, "place_data": None}

    @tool
    def select_best_place(place_id: str, reasoning: str = "") -> Dict[str, Any]:
        """Select the best matching place from the search results.

        Use this tool to explicitly indicate which place_id is the best match
        for the merchant on the receipt. This should be called after evaluating
        multiple search results.

        Args:
            place_id: The Google Places place_id of the best matching business
            reasoning: Optional explanation of why this place was selected

        Returns:
            Confirmation that the place was selected
        """
        # Store the selected place_id for later extraction
        selected_place_result["place_id"] = place_id
        result = {
            "status": "selected",
            "place_id": place_id,
        }
        if reasoning:
            result["reasoning"] = reasoning
        return result

    tools = places_tools + [select_best_place]

    # Initialize LLM for agent - using Ollama Cloud only
    # Use cloud model with -cloud suffix (latest Ollama Cloud feature, v0.12+)
    model_name = "gpt-oss:120b-cloud"  # Cloud model for better reasoning

    # Note: For tool calling, we don't need format="json" - bind_tools handles that
    # Ollama Cloud supports tool calling natively when tools are bound

    llm = ChatOllama(
        model=model_name,
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"},
            "timeout": 120,  # Longer timeout for agent reasoning
        },
        # Don't set format here - bind_tools will handle tool calling format
        temperature=0.3,
    )

    # Bind tools to LLM (Ollama supports tool calling natively)
    # This automatically configures the model to use tool calling format
    llm_with_tools = llm.bind_tools(tools)

    # Create system prompt
    system_prompt = """You are an intelligent assistant that searches Google Places API to find merchant information from receipts.

**Context:**
Receipt text comes from OCR (Optical Character Recognition) and may contain errors. You have access to:
- The raw receipt text (formatted_text) - use this to see what was actually OCR'd
- Extracted merchant information (name, address, phone) - may contain OCR errors
- Merchant words/variations - alternative interpretations

**Your Task:**
Find the correct Google Places business match, even if the receipt text contains OCR errors.

**Available Tools (all automatically cached in DynamoDB):**
1. search_by_phone: Search by phone number (fastest, most accurate) - use FIRST if available
2. search_by_address: Search by address (returns lat/lng for nearby searches)
3. search_nearby: Find nearby businesses (requires lat/lng from address search)
4. search_by_text: Text search for business name (can use location bias with lat/lng)
5. get_place_details: Get complete place information including phone number, website, hours, rating - use this after finding a place to get full details

**Search Strategy:**
1. **Phone first**: If phone is available, use `search_by_phone` - it's the most reliable
2. **Address with location**: If address is available, use `search_by_address` to get lat/lng, then `search_nearby`
3. **Text search with variations**: If only merchant name is available, use `search_by_text`
   - Try the extracted merchant name as-is
   - If that fails, analyze the raw receipt text to identify potential OCR errors
   - Try corrected variations based on common OCR mistakes (character misreads, spacing issues, etc.)
   - Try common business name formats (e.g., "IN N OUT" → "In-N-Out")
   - Use location bias if you have partial address info (city/state)
4. **Be creative**: Combine approaches - e.g., search by text with location bias from address

**OCR Error Reasoning:**
- Look at the raw receipt text to understand what was actually OCR'd
- Common OCR issues: character misreads (rn→m, 0→O, 1→l/I), spacing problems, case issues
- If a word looks like it could be a common place name or business name with a typo, try correcting it
- Think about what the text *should* say based on context (e.g., if you see "Mestlake" near "Village", it's likely "Westlake")
- Try multiple variations - the API is cached, so repeated searches are fast

**Important:**
- All API calls are cached, so feel free to try multiple variations
- You MUST use the tools to search - don't just respond with text
- Be persistent and creative - if one search fails, try variations
- After finding a place, you can use `get_place_details` to get complete information including phone number
- After finding the best match, use `select_best_place` to explicitly indicate which place_id is correct
- If no good match is found after trying all reasonable variations, don't call select_best_place

**Workflow:**
1. Analyze available information (phone, address, merchant name, raw receipt text)
2. Use appropriate search tools in priority order (phone > address > text)
3. If searches fail, reason about potential OCR errors and try corrected variations
4. Evaluate all results to find the best match
5. If a place is found but missing phone number, use `get_place_details` to get complete information
6. Call `select_best_place` with the best matching place_id
"""

    # Build search query from extracted information
    search_parts = []
    if state.extracted_phone:
        search_parts.append(f"Phone: {state.extracted_phone}")
    if state.extracted_address:
        search_parts.append(f"Address: {state.extracted_address}")
    if state.extracted_merchant_name:
        search_parts.append(f"Extracted merchant name: {state.extracted_merchant_name}")
    if state.extracted_merchant_words:
        search_parts.append(f"Merchant word variations: {', '.join(state.extracted_merchant_words)}")

    # Include raw receipt text so agent can see what was actually OCR'd
    if state.formatted_text:
        search_parts.append(f"\nRaw receipt text (may contain OCR errors):\n{state.formatted_text}")

    search_query = "\n".join(search_parts) if search_parts else "No merchant information extracted from receipt."

    try:
        # Manual tool calling loop (simpler approach for Ollama)
        messages: List = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"""Search Google Places for this merchant:

{search_query}

**Instructions:**
- Use the raw receipt text to understand what was actually OCR'd
- If the extracted merchant name doesn't work, analyze the receipt text for potential OCR errors
- Try multiple variations and corrections based on your analysis
- Be persistent - the API is cached, so trying variations is fast
- After finding the best match, use `select_best_place` to confirm the place_id"""),
        ]

        search_results = []
        all_places = []
        selected_place_id = None
        max_iterations = 8  # Increased to allow more OCR correction attempts
        iteration = 0

        # Tool execution helper
        tool_map = {tool.name: tool for tool in tools}

        while iteration < max_iterations:
            iteration += 1
            # Get LLM response
            response = await llm_with_tools.ainvoke(messages)
            messages.append(response)

            # Check if LLM wants to call tools
            # LangChain AIMessage has tool_calls as a list of ToolCall objects
            tool_calls = None
            if hasattr(response, 'tool_calls') and response.tool_calls:
                tool_calls = response.tool_calls
            elif hasattr(response, 'additional_kwargs') and response.additional_kwargs.get('tool_calls'):
                tool_calls = response.additional_kwargs['tool_calls']

            if tool_calls:
                # Debug: print raw tool calls to understand structure
                print(f"   🔧 Tool calls in iteration {iteration}: {len(tool_calls)} call(s)")
                if iteration == 1:  # Debug first iteration
                    print(f"   Debug: response type: {type(response)}")
                    print(f"   Debug: tool_calls type: {type(tool_calls)}")
                    if tool_calls:
                        print(f"   Debug: first tool_call type: {type(tool_calls[0])}")
                        print(f"   Debug: first tool_call: {tool_calls[0]}")

                # Execute each tool call
                for tool_call in tool_calls:
                    # Handle LangChain ToolCall objects (most common)
                    if hasattr(tool_call, 'name'):
                        tool_name = tool_call.name
                        tool_args = tool_call.get('args', {}) if hasattr(tool_call, 'get') else (tool_call.args if hasattr(tool_call, 'args') else {})
                        tool_call_id = tool_call.get('id', '') if hasattr(tool_call, 'get') else (tool_call.id if hasattr(tool_call, 'id') else '')
                    # Handle dict format
                    elif isinstance(tool_call, dict):
                        tool_name = tool_call.get('function', {}).get('name') or tool_call.get('name') or tool_call.get('tool_name')
                        tool_args = tool_call.get('function', {}).get('arguments') or tool_call.get('args', {}) or tool_call.get('arguments', {})
                        tool_call_id = tool_call.get('id', '') or tool_call.get('tool_call_id', '')
                        # Parse arguments if it's a string
                        if isinstance(tool_args, str):
                            import json
                            try:
                                tool_args = json.loads(tool_args)
                            except Exception:
                                tool_args = {}
                    else:
                        # Fallback: try to get attributes
                        tool_name = getattr(tool_call, 'name', None) or getattr(tool_call, 'tool_name', None)
                        tool_args = getattr(tool_call, 'args', {}) or getattr(tool_call, 'arguments', {})
                        tool_call_id = getattr(tool_call, 'id', '') or getattr(tool_call, 'tool_call_id', '')

                    if not tool_name:
                        print(f"   ⚠️  Could not extract tool name from: {tool_call}")
                        continue

                    print(f"   🔧 Calling tool: {tool_name} with args: {tool_args}")

                    if tool_name in tool_map:
                        try:
                            # Execute tool
                            tool_result = await tool_map[tool_name].ainvoke(tool_args)

                            # Store result with detailed logging
                            if tool_name == "select_best_place":
                                if isinstance(tool_result, dict) and tool_result.get("place_id"):
                                    selected_place_id = tool_result["place_id"]
                                    print(f"   ✅ Agent explicitly selected place_id: {selected_place_id}")
                                    if tool_result.get("reasoning"):
                                        print(f"   Reasoning: {tool_result.get('reasoning')}")
                            else:
                                # Store place results with debug logging
                                if isinstance(tool_result, dict):
                                    # Log what we actually got
                                    result_keys = list(tool_result.keys()) if tool_result else []

                                    if tool_result.get("place_id"):
                                        all_places.append(tool_result)
                                        search_results.append(tool_result)
                                        print(f"   ✅ Found place: {tool_result.get('name', 'Unknown')} ({tool_result.get('place_id', 'N/A')[:20]}...)")
                                    elif tool_result:
                                        # Result exists but no place_id - log full structure
                                        print(f"   ⚠️  Tool returned result but no place_id")
                                        print(f"      Keys: {result_keys}")
                                        print(f"      Sample values: {str(tool_result)[:200]}")
                                        # Still try to use it if it has name/address (might be a valid result)
                                        if tool_result.get("name") or tool_result.get("formatted_address"):
                                            print(f"      ⚠️  Result has name/address but no place_id - might be invalid")
                                    else:
                                        # Empty result - check if this is a billing/API error
                                        # The error message should be in the logs from PlacesAPI
                                        print(f"   ⚠️  Tool returned empty result (empty dict)")
                                        # Note: Check logs above for API error details (REQUEST_DENIED, etc.)
                                elif isinstance(tool_result, list):
                                    if tool_result:
                                        print(f"   📋 Tool returned {len(tool_result)} result(s)")
                                        for i, place in enumerate(tool_result):
                                            if isinstance(place, dict):
                                                if place.get("place_id"):
                                                    all_places.append(place)
                                                    search_results.append(place)
                                                    print(f"   ✅ Found place {i+1}: {place.get('name', 'Unknown')} ({place.get('place_id', 'N/A')[:20]}...)")
                                                else:
                                                    print(f"   ⚠️  Place {i+1} has no place_id: {list(place.keys())[:5]}")
                                            else:
                                                print(f"   ⚠️  Place {i+1} is not a dict: {type(place)}")
                                    else:
                                        print(f"   ⚠️  Tool returned empty list")
                                else:
                                    print(f"   ⚠️  Tool returned unexpected type: {type(tool_result)}")

                            # Add tool result to messages
                            # Format tool result as JSON string for better parsing by LLM
                            import json
                            if isinstance(tool_result, (dict, list)):
                                tool_result_str = json.dumps(tool_result, indent=2)
                            else:
                                tool_result_str = str(tool_result)

                            messages.append(ToolMessage(
                                content=tool_result_str,
                                tool_call_id=tool_call_id,
                            ))
                        except Exception as tool_error:
                            print(f"   ⚠️  Error executing tool {tool_name}: {tool_error}")
                            traceback.print_exc()
                            messages.append(ToolMessage(
                                content=f"Error: {str(tool_error)}",
                                tool_call_id=tool_call_id,
                            ))
                    else:
                        print(f"   ⚠️  Unknown tool: {tool_name}")
            else:
                # No more tool calls, agent is done
                print(f"   ✅ Agent completed after {iteration} iteration(s)")
                if hasattr(response, 'content'):
                    print(f"   Final response: {response.content[:200]}...")
                break

        # Find the selected place by place_id
        selected_place = None
        print(f"\n   📊 Search Summary: Found {len(all_places)} place(s) with place_id")
        if all_places:
            for i, place in enumerate(all_places, 1):
                print(f"      {i}. {place.get('name', 'Unknown')} - {place.get('formatted_address', 'N/A')[:50]}...")

        if selected_place_id:
            # Find the place data for the selected place_id
            for place in all_places:
                if place.get("place_id") == selected_place_id:
                    selected_place = place
                    break
            if not selected_place:
                print(f"   ⚠️  Agent selected place_id {selected_place_id} but place data not found in results")
            else:
                print(f"   ✅ Using agent-selected place: {selected_place.get('name', 'Unknown')}")
        elif all_places:
            # Fallback: use the first place found (most relevant)
            selected_place = all_places[0]
            print(f"   ⚠️  Agent didn't explicitly select a place, using first result: {selected_place.get('name', 'Unknown')}")
        else:
            print(f"   ⚠️  No place selected - no valid results found from any search")
            print(f"   💡 TIP: Check the logs above for API errors (e.g., REQUEST_DENIED may indicate billing not enabled)")

        # Enrich selected place with full details (including phone number) if we have a place_id
        if selected_place and selected_place.get("place_id"):
            place_id = selected_place.get("place_id")
            # Check if we already have phone number
            if not selected_place.get("formatted_phone_number"):
                print(f"   📞 Enriching place details to get phone number...")
                try:
                    from receipt_label.data.places_api import PlacesAPI
                    places_api = PlacesAPI(api_key=google_places_api_key)
                    full_details = places_api.get_place_details(place_id)
                    if full_details:
                        # Merge full details into selected_place (full_details has priority)
                        selected_place = {**selected_place, **full_details}
                        if full_details.get("formatted_phone_number"):
                            print(f"   ✅ Got phone number: {full_details.get('formatted_phone_number')}")
                        else:
                            print(f"   ⚠️  Place details retrieved but no phone number available")
                    else:
                        print(f"   ⚠️  Could not retrieve place details")
                except Exception as e:
                    print(f"   ⚠️  Error enriching place details: {e}")
            else:
                print(f"   ✅ Place already has phone number: {selected_place.get('formatted_phone_number')}")

        return {
            "places_search_results": search_results,
            "selected_place": selected_place,
        }

    except Exception as e:
        print(f"   ❌ Error searching Places API with agent: {e}")
        traceback.print_exc()
        return {
            "places_search_results": [],
            "selected_place": None,
            "error_count": state.error_count + 1,
            "last_error": str(e),
        }

