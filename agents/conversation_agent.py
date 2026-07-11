import asyncio
import os
import json
import logging
import time
import traceback
from typing import Dict, Any, List, Optional, TypedDict, Annotated
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
import re

# LangGraph imports
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.graph.message import add_messages

# LangChain imports
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from langchain_classic.memory import ConversationBufferWindowMemory



from models.emly_messages import EMLYMessages
from config import MODEL, EMBEDDING_MODEL_NAME, LATEST_N_MESSAGES, \
    EMBEDDING_MODEL_INSTANCE, OPENAI_API_KEY, OPENAI_BASE_URL, TOP_K, TEMPERATURE, \
    INTENT_DETECTION_PROMPT, DEFAULT_MESSAGE

from agents.rag_manager import get_rag_manager
from routes.actions import get_config_json_file
from cachetools import TTLCache
from utils.utils import clean_template_string, get_filtered_citations, filter_citations


def _serialize_citations(citations: List[Any]) -> Optional[str]:
    """Serialize a list of RAG citations for storage on `EMLYMessage.citations`.

    The runtime carries citations as ``{"metadata": {...}, "chunk": "..."}``
    dicts. We persist only the lightweight metadata needed for the UI's
    citation chip (file_id, filename, source_url, score) and drop the chunk
    text — Qdrant is the source of truth for the chunk body, and storing it
    twice doubles row size on busy bots.
    """
    if not citations:
        return None
    items: List[Dict[str, Any]] = []
    for c in citations:
        if not isinstance(c, dict):
            continue
        meta = c.get("metadata") if isinstance(c.get("metadata"), dict) else c
        items.append(
            {
                "file_id": meta.get("file_id"),
                "filename": meta.get("filename") or meta.get("source"),
                "source_url": meta.get("source_url"),
                "score": meta.get("relevance_score") or c.get("score"),
            }
        )
    if not items:
        return None
    try:
        return json.dumps(items, ensure_ascii=False, default=str)
    except Exception:
        return None


log = logging.getLogger(__name__)

MAX_SLOT_FILLING_ATTEMPTS = 3


# --- Data Structures ---

class ConversationState(TypedDict):
    """State that flows through the agent workflow."""
    messages: Annotated[List[BaseMessage], add_messages]
    user_input: str
    current_topic: Optional[str]
    detected_intent: Optional[str]
    intent_confidence: float
    filled_slots: Dict[str, Any]
    missing_slots: List[str]
    rag_context: str
    response: Any
    needs_slot_filling: bool
    conversation_complete: bool
    global_context: Dict[str, Any]
    citations: list
    stream: bool
    page_id: Optional[str] = None
    global_history: str
    slot_filling_attempts: int
    is_slot_filling_active: bool
    skip_router: bool
    # Phase 3 backend-backfill: channel_id flows from the dispatcher /
    # widget chat ingress through `process_user_input` so the persistence
    # path can record per-message channel attribution. Optional because
    # legacy callers may not know it.
    channel_id: Optional[str] = None

@dataclass
class SlotDefinition:
    """Defines a piece of information to be collected (a "slot")."""
    name: str
    slot_type: str
    required: bool = True
    description: Optional[str] = None
    prompt_template: Optional[str] = None

@dataclass
class TopicConfig:
    """Configuration for a specific conversational topic."""
    name: str
    description: str
    slots: List[SlotDefinition] = field(default_factory=list)
    prompts: Dict[str, str] = field(default_factory=dict)
    requires_rag: bool = False
    # New flag to control agent behavior directly from config
    skip_slot_filling: bool = False

    def __post_init__(self):
        """Ensure that slot dictionaries are converted to SlotDefinition objects."""
        if self.slots and isinstance(self.slots[0], dict):
            self.slots = [SlotDefinition(**s) for s in self.slots if isinstance(s, dict)]


@dataclass
class IntentDetectionResult:
    """Result of an intent detection operation."""
    intent: str
    confidence: float
    is_definitive: bool

# --- Core Components ---

class PromptManager:
    """Manages and formats prompts for the assistant."""
    def __init__(self, global_prompts: Dict[str, str], topics: Dict[str, TopicConfig]):
        self.global_prompts = global_prompts
        self.topics = topics

    def get_prompt(self, prompt_name: str, topic: Optional[str] = None) -> Optional[str]:
        """Get a prompt template, checking topic-specific prompts first."""
        if topic and topic in self.topics:
            return self.topics[topic].prompts.get(prompt_name) or self.global_prompts.get(prompt_name)
        return self.global_prompts.get(prompt_name)

    def format_prompt(self, prompt_name: str, data: Dict[str, Any], topic: Optional[str] = None) -> str:
        """Format a prompt with the given data."""
        template = self.get_prompt(prompt_name, topic)
        if not template:
            # Fallback for critical prompts
            if prompt_name == "slot_question":
                return f"Could you please provide your {data.get('slot_name', 'details')}?"
            raise ValueError(f"Prompt '{prompt_name}' not found for topic '{topic}' or globally.")
        return template.format(**data)

class SlotManager:
    """Manages slot filling and state for all topics."""
    def __init__(self, topics: Dict[str, TopicConfig], llm: ChatOpenAI):
        self.topics = topics
        self.llm = llm
        self.filled_slots: Dict[str, Dict[str, Any]] = {topic: {} for topic in topics}
        self.logger = logging.getLogger(__name__)

    def extract_slots(self, user_input: str, topic_name: str) -> Dict[str, Any]:
        """Extracts slots from user input using an LLM call."""
        topic_config = self.topics.get(topic_name)
        
        if not topic_config or not topic_config.slots:
            return {}

        slot_definitions = topic_config.slots
        prompt = self._build_slot_extraction_prompt(user_input, slot_definitions)
        
        try:
            response = self.llm.invoke(prompt)
            # Basic regex to find a JSON object
            content = response.content if isinstance(response.content, str) else str(response.content)
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if not match:
                self.logger.error("No JSON object found in LLM response for slot extraction.")
                return {}

            extracted_data = json.loads(match.group())
            
            # Validate and update slots
            for slot_def in slot_definitions:               
                
                slot_name = slot_def.name
                if slot_name in extracted_data and extracted_data[slot_name] is not None:
                    self.filled_slots[topic_name][slot_name] = extracted_data[slot_name]

            return self.get_filled_slots(topic_name)
        except (json.JSONDecodeError, KeyError) as e:
            self.logger.error(f"Error parsing LLM response for slot extraction: {e}")
            return {}

    def get_filled_slots(self, topic_name: str) -> Dict[str, Any]:
        """Get all currently filled slots for a topic."""
        return self.filled_slots.get(topic_name, {})

    def get_missing_slots(self, topic_name: str) -> List[SlotDefinition]:
        """Get a list of required slots that are not yet filled."""
        topic_config = self.topics.get(topic_name)
        if not topic_config:
            return []
        
        filled = self.get_filled_slots(topic_name)
        return [
            slot for slot in topic_config.slots
            if slot.required and slot.name not in filled
        ]

    def _build_slot_extraction_prompt(self, user_input: str, slots: List[SlotDefinition]) -> str:
        """Builds the prompt for the LLM to extract slots."""
        slot_descriptions = "\n".join(
            f'- "{s.name}": {s.description} (type: {s.slot_type})' for s in slots
        )
        return f"""
        From the user's input, extract the following information.
        Return ONLY a valid JSON object. If a value is not found, use null.

        User Input: "{user_input}"

        Schema:
        {slot_descriptions}

        JSON Output:
        """
    
    def reset_slots(self, topic_name: str):
        """Reset all slots for a given topic."""
        if topic_name in self.filled_slots:
            self.filled_slots[topic_name] = {}

class IntentRouter:
    """Detects user intent and routes to the appropriate topic."""
    def __init__(self, llm: ChatOpenAI, topics: Dict[str, TopicConfig]):
        self.llm = llm
        self.topics = topics
        self.logger = logging.getLogger(__name__)

    def detect_intent(self, user_input: str, available_topics: List[str], context: Dict[str, Any], history: str, current_topic: str | None = "") -> IntentDetectionResult:
        """Detects the most likely intent from the user input."""
        topic_descriptions = "\n".join(
            f'- "{name}": {config.description}' for name, config in self.topics.items() if name in available_topics
        )
        prompt = f"""
        {INTENT_DETECTION_PROMPT}

        Topics Start:
        {topic_descriptions}
        Topics End.

        Current Topic Start: 
        {current_topic}
        Current Topic End.

        Conversation History Start:
        {history}
        Conversation History End.

        User Input Start: 
        "{user_input}"
        User Input End.

        Respond with a JSON object with 'intent' and 'confidence' keys.
        'intent' must be one of the topic names.
        'confidence' is a float from 0.0 to 1.0.
        
        JSON Response:
        """
        try:
            response = self.llm.invoke(prompt)
            content = response.content if isinstance(response.content, str) else str(response.content)
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if not match:
                self.logger.error("No JSON object found in LLM response for intent detection.")
                return IntentDetectionResult(intent=available_topics[0], confidence=0.5, is_definitive=False)

            result = json.loads(match.group())
            intent = result.get("intent", "general_chat")
            confidence = float(result.get("confidence", 0.5))
            
            if intent not in available_topics:
                intent = available_topics[0]
            
            return IntentDetectionResult(intent=intent, confidence=confidence, is_definitive=confidence > 0.7)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            self.logger.error(f"Error detecting intent: {e}")
            return IntentDetectionResult(intent=available_topics[0], confidence=0.5, is_definitive=False)

class RAGManager:
    """Per-bot RAG facade.

    Phase 0 thin shim that delegates to the process-shared
    ``agents.rag_manager.RAGManager`` (singleton via ``get_rag_manager()``).
    Each topic that has ``requires_rag=True`` shares the bot's single
    Qdrant tenant — there's no longer a per-topic vector store.
    """
    def __init__(self, topics: Dict[str, TopicConfig], bot_id: str, embeddings=None, db_path: str | None = None):
        self.topics = topics
        self.bot_id = bot_id
        self.rag = get_rag_manager()
        self.logger = logging.getLogger(__name__)
        self._rag_topics = {name for name, cfg in topics.items() if cfg.requires_rag}
        if self._rag_topics:
            self.logger.info("RAG enabled for topics: %s (bot=%s)", sorted(self._rag_topics), self.bot_id)

    def should_use_rag(self, topic_name: str) -> bool:
        return topic_name in self._rag_topics

    def get_context(self, query: str, topic_name: str, k: int = 3) -> tuple[str, list[Any]]:
        if not self.should_use_rag(topic_name):
            self.logger.warning("RAG is not enabled for topic '%s'.", topic_name)
            return "", []
        threshold = float(get_config_json_file(self.bot_id).get("embedding_threshold", 0.20))
        return self.rag.search(
            bot_id=self.bot_id,
            query=query,
            top_k=k,
            embedding_threshold=threshold,
        )

class ConversationManager:
    """Manages conversation history and context across topics."""
    def __init__(self, bot_id: str, user_id: str, session_id: str, topics: Dict[str, TopicConfig], window_size: int = 10):
        self.global_memory = ConversationBufferWindowMemory(k=window_size, return_messages=True)
        self.topic_memories: Dict[str, ConversationBufferWindowMemory] = {
            name: ConversationBufferWindowMemory(k=window_size, return_messages=True)
            for name in topics
        }
        self.current_topic: Optional[str] = None
        self.global_context: Dict[str, Any] = {}
        self.bot_id = bot_id
        self.user_id = user_id
        self.session_id = session_id
        self.window_size = window_size

        self.slot_filling_state: Dict[str, Dict[str, Any]] = {
            name: {
                "is_active": False,
                "attempts": 0,
                "last_question_slot": None
            }
            for name in topics
        }

        self.load_topic_memory_with_messages()
        self.load_global_memory_with_messages()

    def load_topic_memory_with_messages(self):
        for topic_name in self.topic_memories.keys():
            messages = EMLYMessages.get_messages_by_topic(
                self.bot_id,
                self.user_id,
                self.session_id,
                topic_name,
                limit=self.window_size * 2,
            )

            formatted_messages = []
            for msg in messages:
                if msg.role == "user":
                    formatted_messages.append(HumanMessage(content=msg.message))
                elif msg.role == "assistant":
                    formatted_messages.append(AIMessage(content=msg.message))
                else:
                    # fallback if some system or unknown role appears
                    formatted_messages.append({"role": msg.role, "content": msg.message})
            formatted_messages.reverse()

            self.topic_memories[topic_name].chat_memory.messages = formatted_messages
    
    def  load_global_memory_with_messages(self):
        messages = EMLYMessages.get_messages(
            self.bot_id,
            self.user_id,
            self.session_id,
            limit=self.window_size * 2
        )

        formatted_messages = []
        for msg in messages:
            if msg.role == "user":
                formatted_messages.append(HumanMessage(content=msg.message))
            elif msg.role == "assistant":
                formatted_messages.append(AIMessage(content=msg.message))
            else:
                # fallback if some system or unknown role appears
                formatted_messages.append({"role": msg.role, "content": msg.message})
        formatted_messages.reverse()

        self.global_memory.chat_memory.messages = formatted_messages



    def get_topic_memory(self, topic_name: str) -> ConversationBufferWindowMemory:
        """Get the memory for a specific topic."""
        return self.topic_memories[topic_name]
    
    def get_slot_filling_state(self, topic_name: str) -> Dict[str, Any]:
        """Get slot-filling state for a topic."""
        return self.slot_filling_state.get(topic_name, {
            "is_active": False, 
            "attempts": 0, 
            "last_question_slot": None
        })

    def update_slot_filling_state(self, topic_name: str, is_active: bool, 
                                  attempts: int = 0, last_question_slot: Optional[str] = None):
        """Update slot-filling state for a topic."""
        if topic_name in self.slot_filling_state:
            self.slot_filling_state[topic_name]["is_active"] = is_active
            self.slot_filling_state[topic_name]["attempts"] = attempts
            self.slot_filling_state[topic_name]["last_question_slot"] = last_question_slot

    def reset_slot_filling_state(self, topic_name: str):
        """Reset slot-filling state for a topic."""
        if topic_name in self.slot_filling_state:
            self.slot_filling_state[topic_name] = {
                "is_active": False,
                "attempts": 0,
                "last_question_slot": None
            }

# --- Agent Definitions ---

class BaseAgent(ABC):
    """Abstract base class for all agents."""
    def __init__(self, name: str, llm: ChatOpenAI):
        self.name = name
        self.llm = llm
        self.logger = logging.getLogger(f"{__name__}.{name}")

    @abstractmethod
    def process(self, state: ConversationState) -> ConversationState:
        """Process the conversation state and return the updated state."""
        pass

class RouterAgent(BaseAgent):
    """Agent responsible for routing conversations to appropriate topic agents."""
    def __init__(self, llm: ChatOpenAI, intent_router: IntentRouter, available_topics: List[str]):
        super().__init__("router", llm)
        self.intent_router = intent_router
        self.available_topics = available_topics

    def process(self, state: ConversationState) -> ConversationState:
        """Detect intent and route to the appropriate agent."""
        try:
            intent_result = self.intent_router.detect_intent(
                state["user_input"],
                self.available_topics,
                state.get("global_context", {}, ),
                state.get("global_history", ""),
                state.get("current_topic", "")
            )
            
            state["detected_intent"] = intent_result.intent
            state["intent_confidence"] = intent_result.confidence
            
            if intent_result.is_definitive and intent_result.intent != state.get("current_topic"):
                state["current_topic"] = intent_result.intent
            elif not state.get("current_topic"):
                state["current_topic"] = intent_result.intent
            
            return state
        except Exception as e:
            self.logger.error(f"Error in router agent: {e}")
            state["current_topic"] = self.available_topics[0] if self.available_topics else "general_chat"
            return state

class TopicAgent(BaseAgent):
    """A single, data-driven agent for handling all specific topics."""
    def __init__(self, topic_name: str, llm: ChatOpenAI, config: TopicConfig, 
                 slot_manager: SlotManager, rag_manager: RAGManager, 
                 prompt_manager: PromptManager, conversation_manager: ConversationManager,
                 user_id: str, session_id: str):
        super().__init__(f"{topic_name}_agent", llm)
        self.topic_name = topic_name
        self.config = config
        self.slot_manager = slot_manager
        self.rag_manager = rag_manager
        self.prompt_manager = prompt_manager
        self.conversation_manager = conversation_manager
        self.user_id = user_id
        self.session_id = session_id

    def process(self, state: ConversationState) -> ConversationState:
        """Process conversation for this topic based on its configuration."""
        stream = state.get("stream", False)
        try:
            user_input = state["user_input"]
            topic_memory = self.conversation_manager.get_topic_memory(self.topic_name)
            
            if self.config.skip_slot_filling:
                response_state = self._handle_direct_response(state, user_input, topic_memory)
            else:
                response_state = self._handle_slot_based_response(state, user_input, topic_memory)
            
            slot_state = self.conversation_manager.get_slot_filling_state(self.topic_name)
            self.conversation_manager.update_slot_filling_state(
                self.topic_name,
                is_active=response_state.get("is_slot_filling_active", False),
                attempts=response_state.get("slot_filling_attempts", 0),
                last_question_slot=slot_state.get("last_question_slot")
            )

            # For non-streaming, save the message now.
            # For streaming, the message will be saved in the AgenticConversationHandler after the stream is complete.
            if not stream:
                topic_memory.chat_memory.add_user_message(user_input)
                topic_memory.chat_memory.add_ai_message(response_state["response"])
                # Phase 2 backend backfill: pull telemetry off the state
                # (set by `_generate_final_response`) and persist it on the
                # assistant row. User rows carry no LLM telemetry.
                tele = response_state.get("_llm_telemetry") or {}
                citations_list = response_state.get("citations") or []
                citations_json = _serialize_citations(citations_list)
                channel_id = state.get("channel_id")
                # Phase 6 backend-backfill: heuristic deflection labelling.
                # Returns None when the per-bot opt-in flag is off, leaving
                # the column NULL — admins flip it via the API later.
                from services.deflection import compute_deflection
                deflection_result = compute_deflection(
                    self.conversation_manager.bot_id,
                    response_state.get("response") or "",
                )
                is_deflected = deflection_result[0] if deflection_result else None
                deflection_method = deflection_result[1] if deflection_result else None
                EMLYMessages.insert_new_message(
                    bot_id=self.conversation_manager.bot_id,
                    user_id=self.user_id,
                    session_id=self.session_id,
                    topic=self.topic_name,
                    message=user_input,
                    not_useful=False,
                    expanded_query=None,
                    page=state.get("page_id"),
                    role="user",
                    channel_id=channel_id,
                )
                EMLYMessages.insert_new_message(
                    bot_id=self.conversation_manager.bot_id,
                    user_id=self.user_id,
                    session_id=self.session_id,
                    topic=self.topic_name,
                    message=response_state["response"],
                    not_useful=False,
                    expanded_query=None,
                    page=state.get("page_id"),
                    role="assistant",
                    channel_id=channel_id,
                    model_used=tele.get("model"),
                    prompt_tokens=tele.get("prompt_tokens"),
                    completion_tokens=tele.get("completion_tokens"),
                    response_time_ms=tele.get("response_time_ms"),
                    citations=citations_json,
                    is_deflected=is_deflected,
                    deflection_method=deflection_method,
                )
            return response_state
        except Exception as e:
            self.logger.error(f"Error in {self.topic_name} agent: {e}")
            traceback.print_exc()
            if stream:
                def error_stream():
                    yield "I'm sorry, I encountered an error. Could you please try again?"
                state["response"] = error_stream()
                state["conversation_complete"] = False
                return state
            state["response"] = "I'm sorry, I encountered an error. Could you please try again?"
            state["conversation_complete"] = False
            return state

    def _handle_direct_response(self, state: ConversationState, user_input: str, memory) -> ConversationState:
        """Handle topics that don't require slot filling (e.g., general chat)."""
        stream = state.get("stream", False)
        response, citations, telemetry = self._generate_final_response(user_input, {}, memory, stream=stream)
        state["response"] = response
        state["conversation_complete"] = True
        state["needs_slot_filling"] = False
        state["filled_slots"] = {}
        state["missing_slots"] = []
        state["citations"] = citations
        state["_llm_telemetry"] = telemetry
        state["is_slot_filling_active"] = False
        state["slot_filling_attempts"] = 0
        state["skip_router"] = False
        return state

    def _handle_slot_based_response(self, state: ConversationState, user_input: str, memory) -> ConversationState:
        """Handle topics that use slot filling."""
        stream = state.get("stream", False)
        slot_state = self.conversation_manager.get_slot_filling_state(self.topic_name)
        
        self.slot_manager.extract_slots(user_input, self.topic_name)
        current_slots = self.slot_manager.get_filled_slots(self.topic_name)
        missing_slots = self.slot_manager.get_missing_slots(self.topic_name)

        state["filled_slots"] = current_slots
        state["missing_slots"] = [slot.name for slot in missing_slots]
        state["needs_slot_filling"] = bool(missing_slots)

        if missing_slots:

            # Increment attempts if we're still in slot-filling mode
            attempts = state.get("slot_filling_attempts", 0)
            if slot_state["is_active"]:
                attempts += 1

             # Check if we've exceeded max attempts
            if attempts >= MAX_SLOT_FILLING_ATTEMPTS:
                self.logger.warning(f"Max slot-filling attempts ({MAX_SLOT_FILLING_ATTEMPTS}) reached for topic '{self.topic_name}'. Re-routing to router.")
                # Reset state and force re-routing
                state["slot_filling_attempts"] = 0
                state["is_slot_filling_active"] = False
                state["skip_router"] = False
                self.conversation_manager.reset_slot_filling_state(self.topic_name)
                self.slot_manager.reset_slots(self.topic_name)

                # Generate a helpful message
                response = "I'm having trouble understanding. Let me help you differently. What would you like to know?"
                if stream:
                    def on_Stream():
                        yield response
                    state["response"] = on_Stream()
                else:
                    state["response"] = response
                state["conversation_complete"] = False
                return state

            response = self._generate_slot_question(missing_slots[0])
            if stream:
                def on_stream():
                    yield response
                state["response"] = on_stream()
            else:
                state["response"] = response

            state["conversation_complete"] = False
            state["is_slot_filling_active"] = True
            state["slot_filling_attempts"] = attempts
            state["skip_router"] = True  # KEY: Skip router for next user response
            
            # Track which slot we asked about
            self.conversation_manager.update_slot_filling_state(
                self.topic_name,
                is_active=True,
                attempts=attempts,
                last_question_slot=missing_slots[0].name
            )
        else:
            response, citations, telemetry = self._generate_final_response(user_input, current_slots, memory, stream=stream)
            state["response"] = response
            state["conversation_complete"] = True
            state["citations"] = citations
            state["_llm_telemetry"] = telemetry
            state["is_slot_filling_active"] = False
            state["slot_filling_attempts"] = 0
            state["skip_router"] = False

            # Reset slot-filling state
            self.conversation_manager.reset_slot_filling_state(self.topic_name)
        return state

    def _generate_slot_question(self, slot_def: SlotDefinition) -> str:
        """Generate a question to ask the user for a missing slot."""
        if slot_def.prompt_template:
            return slot_def.prompt_template
        
        return self.prompt_manager.format_prompt(
            "slot_question",
            {"slot_name": slot_def.name.replace('_', ' '), "slot_description": slot_def.description or ""},
            self.topic_name
        )

    def _generate_final_response(self, user_input: str, filled_slots: Dict[str, Any], memory, stream: bool = False) -> tuple[Any, list[Any], Dict[str, Any]]:
        """Generate the final response using RAG or a direct LLM call.

        Returns ``(response, citations, telemetry)``. Telemetry carries the
        Phase 2 backfill fields: ``model``, ``prompt_tokens``,
        ``completion_tokens``, ``response_time_ms``. For streaming responses
        token counts stay ``None`` (the streaming usage capture is wired
        via ``stream_options.include_usage`` but not yet plumbed through
        the LangGraph stream chunks — separate follow-up).
        """
        citations = []
        telemetry: Dict[str, Any] = {
            "model": getattr(self.llm, "model_name", None) or getattr(self.llm, "model", None),
            "prompt_tokens": None,
            "completion_tokens": None,
            "response_time_ms": None,
        }
        if self.rag_manager.should_use_rag(self.topic_name):
            search_query = " ".join([user_input] + [str(v) for v in filled_slots.values() if v])

            context, citations = self.rag_manager.get_context(search_query, self.topic_name, TOP_K)

            if context == "":
                if stream:
                    def no_context_stream():
                        yield DEFAULT_MESSAGE
                    return no_context_stream(), citations, telemetry
                return DEFAULT_MESSAGE, citations, telemetry

            prompt_name = "llm_response"
            filtered_citations =  filter_citations(citations)
            citations = get_filtered_citations(citations)

            # ✅ Combine payloads and chunks — if payload is empty, use chunk instead
            formatted_docs = []
            seen_payloads = set()
            payload_count = 0
            for i, item in enumerate(filtered_citations):
                payload = item.get("payload")
                if len(payload)>0:
                    payload_count += 1
                chunk = item.get("chunk")

                # Convert payload dict to JSON string for deduplication
                payload_str = ""
                if payload and isinstance(payload, dict) and len(payload) > 0:
                    try:
                        payload_str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
                    except Exception:
                        payload_str = str(payload)

                # Skip if this payload was already processed
                if payload_str and payload_str in seen_payloads:
                    continue

                if payload_str:
                    doc_content = "\n".join([f"{key} is  {value}" for key, value in payload.items()])
                    seen_payloads.add(payload_str)
                else:
                    doc_content = chunk or ""  # fallback to chunk if payload is missing/empty

                formatted_docs.append(f"**Item {i + 1}  Start** \n{doc_content} \n**Item {i + 1} End**")

            # ✅ Join all formatted docs into context
            final_output = "\n\n".join(formatted_docs)

            if final_output and payload_count>0:
                context = final_output

            data = {"context": context, "user_input": user_input, "filled_slots": json.dumps(filled_slots)}
        else:
            prompt_name = "llm_response"
            data = {"user_input": user_input, "filled_slots": json.dumps(filled_slots), "context": ""}
        
        history_obj = memory.load_memory_variables({}).get('history', [])
        history = "\n".join([f"{'User' if isinstance(m, HumanMessage) else 'Assistant'}: {m.content}" for m in history_obj])
        data["history"] = history
        
        template = self.prompt_manager.get_prompt(prompt_name, self.topic_name)
        if not template:
            # Fallback template if none found
            template = "Please respond to the user's request: {user_input}"
        
        prompt = ChatPromptTemplate.from_template(template)
        t0 = time.perf_counter()
        if stream:
            # Streaming path keeps the StrOutputParser pipeline. We wrap
            # the generator so latency is captured at completion; token
            # counts remain None until the LangGraph stream-chunk plumbing
            # surfaces the final-chunk usage (separate follow-up).
            chain = prompt | self.llm | StrOutputParser()
            base_gen = chain.stream(data)

            def timed_stream():
                try:
                    for chunk in base_gen:
                        yield chunk
                finally:
                    telemetry["response_time_ms"] = int((time.perf_counter() - t0) * 1000)

            return timed_stream(), citations, telemetry

        # Non-streaming: split the chain so the raw `AIMessage` is reachable
        # before `StrOutputParser` strips its `usage_metadata` attribute.
        raw_chain = prompt | self.llm
        result = raw_chain.invoke(data)
        telemetry["response_time_ms"] = int((time.perf_counter() - t0) * 1000)
        # LangChain ≥0.3 sets `usage_metadata` (input_tokens/output_tokens);
        # older builds expose `response_metadata['token_usage']` with the
        # OpenAI-shaped fields. Tolerate both.
        usage = getattr(result, "usage_metadata", None)
        if usage:
            telemetry["prompt_tokens"] = usage.get("input_tokens")
            telemetry["completion_tokens"] = usage.get("output_tokens")
        else:
            legacy = {}
            if hasattr(result, "response_metadata"):
                legacy = (result.response_metadata or {}).get("token_usage", {})
            telemetry["prompt_tokens"] = legacy.get("prompt_tokens")
            telemetry["completion_tokens"] = legacy.get("completion_tokens")
        content = getattr(result, "content", None)
        if content is None:
            content = str(result)
        return content, citations, telemetry

# --- Orchestrator ---

class AgenticConversationHandler:
    """Main orchestrator for the agentic conversation workflow."""
    def __init__(self, config: Dict[str, Any], user_id: str, session_id: str, llm, embeddings, topics, prompt_manager, intent_router, rag_manager, bot_id: str):
        self.config = config
        self.logger = self._setup_logging()
        self.bot_id = bot_id
        self.user_id = user_id
        self.session_id = session_id

        # Shared components passed from ConversationSessionManager
        self.llm = llm
        self.embeddings = embeddings
        self.topics = topics
        self.prompt_manager = prompt_manager
        self.intent_router = intent_router
        self.rag_manager = rag_manager

        # Session-specific components
        self.slot_manager = SlotManager(self.topics, self.llm)
        self.conversation_manager = ConversationManager(bot_id, user_id, session_id, self.topics, LATEST_N_MESSAGES)
        
        self.agents = self._initialize_agents()
        self.workflow = self._create_workflow()

    def _setup_logging(self) -> logging.Logger:
        return logging.getLogger(__name__)

    def _initialize_agents(self) -> Dict[str, BaseAgent]:
        """Initialize all agents in a data-driven way."""
        agents: dict[str, BaseAgent] = dict()
        agents["router"] = RouterAgent(self.llm, self.intent_router, list(self.topics.keys()))
        for topic_name, topic_config in self.topics.items():
            agents[topic_name] = TopicAgent(
                topic_name, self.llm, topic_config, self.slot_manager,
                self.rag_manager, self.prompt_manager, self.conversation_manager,
                self.user_id, self.session_id
            )
        return agents


    def _create_workflow(self):
        """Create the LangGraph workflow."""
        workflow = StateGraph(ConversationState)
        
        num_topics = len(self.topics)
        topic_names = list(self.topics.keys())

        if num_topics == 1:
            single_topic_name = topic_names[0]
            workflow.add_node(single_topic_name, self._run_agent(single_topic_name))
            workflow.set_entry_point(single_topic_name)
            workflow.add_edge(single_topic_name, END)
        else:
            workflow.add_node("router", self._run_agent("router"))
            for topic_name in topic_names:
                workflow.add_node(topic_name, self._run_agent(topic_name))
            
            # KEY CHANGE: Conditional entry point based on skip_router flag
            def should_skip_router(state: ConversationState) -> str:
                if state.get("skip_router", False) and state.get("current_topic"):
                    return state["current_topic"]
                return "router"
            
            # REPLACE set_entry_point with set_conditional_entry_point
            workflow.set_conditional_entry_point(
                should_skip_router,
                {**{topic_name: topic_name for topic_name in topic_names}, "router": "router"}
            )
            
            workflow.add_conditional_edges(
                "router",
                lambda state: state.get("current_topic", topic_names[0]),
                {topic_name: topic_name for topic_name in topic_names}
            )
            
            for topic_name in topic_names:
                workflow.add_edge(topic_name, END)

        ret: CompiledStateGraph = workflow.compile()
        return ret

    def _run_agent(self, agent_name: str):
        """Returns a function that runs the specified agent."""
        def run(state: ConversationState) -> ConversationState:
            return self.agents[agent_name].process(state)
        return run

    def process_user_input(self, user_input: str, stream: bool = False, page_id: Optional[str] = None, channel_id: Optional[str] = None) -> Any:
        """Process user input through the agentic workflow."""
        # Phase 3 backend-backfill: stash the channel id on the handler so
        # both persistence paths (non-streaming inside `TopicAgent.process`
        # and the streaming generator below) can read it without further
        # threading. ConversationState is a TypedDict and adding a new
        # required field would touch every state init across the file —
        # using an attribute keeps the diff small.
        self._current_channel_id = channel_id

        if user_input.lower().startswith("/"):
            return self._handle_command(user_input), []

        current_topic = self.conversation_manager.current_topic
        skip_router = False
        slot_filling_attempts = 0
        is_slot_filling_active = False

        if current_topic:
            slot_state = self.conversation_manager.get_slot_filling_state(current_topic)
            skip_router = slot_state["is_active"]
            slot_filling_attempts = slot_state["attempts"]
            is_slot_filling_active = slot_state["is_active"]

        initial_state = ConversationState(
            messages=[HumanMessage(content=user_input)],
            user_input=user_input,
            current_topic=self.conversation_manager.current_topic,
            detected_intent=None,
            intent_confidence=0.0,
            filled_slots=self.slot_manager.get_filled_slots(self.conversation_manager.current_topic or "") or {},
            missing_slots=[],
            rag_context="",
            response="",
            needs_slot_filling=False,
            conversation_complete=False,
            global_context=self.conversation_manager.global_context,
            citations=[],
            stream=stream,
            page_id=page_id,
            global_history="\n".join([f"{'User' if isinstance(m, HumanMessage) else 'Assistant'}: {m.content}" for m in self.conversation_manager.global_memory.chat_memory.messages]),
            slot_filling_attempts=slot_filling_attempts,
            is_slot_filling_active=is_slot_filling_active,
            skip_router=skip_router,
            channel_id=channel_id,
        )

        try:
            if not stream:
                result = self.workflow.invoke(initial_state)
                if result.get("current_topic"):
                    self.conversation_manager.current_topic = result["current_topic"]
                self.conversation_manager.global_memory.chat_memory.add_user_message(user_input)
                self.conversation_manager.global_memory.chat_memory.add_ai_message(result["response"])
                return result["response"], result.get("citations", [])

            # Streaming logic
            stream_t0 = time.perf_counter()

            def stream_generator():
                full_response = ""
                final_citations = []
                final_telemetry: Dict[str, Any] = {}
                current_topic = self.conversation_manager.current_topic

                for chunk in self.workflow.stream(initial_state):
                    last_node = list(chunk.keys())[-1]
                    last_node_output = chunk[last_node]
                    if "current_topic" in last_node_output:
                        current_topic = last_node_output["current_topic"]
                        self.conversation_manager.current_topic = current_topic


                    if "response" in last_node_output and last_node_output["response"]:
                        if hasattr(last_node_output["response"], '__iter__') and not isinstance(last_node_output["response"], str):
                            for token in last_node_output["response"]:
                                yield {"type": "token", "data": token}
                                full_response += token

                    if "citations" in last_node_output and last_node_output["citations"]:
                        final_citations.extend(last_node_output["citations"])

                    # The TopicAgent's `_handle_*_response` records telemetry
                    # on `state["_llm_telemetry"]` before yielding. The
                    # latest non-empty value wins (router → topic → end).
                    if "_llm_telemetry" in last_node_output and last_node_output["_llm_telemetry"]:
                        final_telemetry = last_node_output["_llm_telemetry"]

                # After stream is complete, update memory and save messages
                self.conversation_manager.global_memory.chat_memory.add_user_message(user_input)
                self.conversation_manager.global_memory.chat_memory.add_ai_message(full_response)
                message_id = None
                # If there's only one topic, set it as current
                if len(list(self.topics.keys())) == 1:
                    current_topic = list(self.topics.keys())[0]
                    self.conversation_manager.current_topic = current_topic
                if current_topic:
                    topic_memory = self.conversation_manager.get_topic_memory(current_topic)
                    topic_memory.chat_memory.add_user_message(user_input)
                    topic_memory.chat_memory.add_ai_message(full_response)

                    # Phase 2 telemetry: prefer the per-node telemetry the
                    # TopicAgent recorded; if it's missing, fall back to a
                    # wall-clock latency measured from `stream_t0`. Token
                    # counts may stay None for streaming today (separate
                    # follow-up parses the final OpenAI usage chunk).
                    elapsed_ms = int((time.perf_counter() - stream_t0) * 1000)
                    model_used = final_telemetry.get("model") or getattr(self.llm, "model_name", None) or getattr(self.llm, "model", None)
                    response_time_ms = final_telemetry.get("response_time_ms") or elapsed_ms
                    citations_json = _serialize_citations(final_citations)

                    stream_channel_id = initial_state.get("channel_id")
                    # Phase 6 backend-backfill: same heuristic deflection
                    # check as the non-streaming path. None when the bot
                    # hasn't opted in.
                    from services.deflection import compute_deflection
                    deflection_result = compute_deflection(self.bot_id, full_response)
                    stream_is_deflected = deflection_result[0] if deflection_result else None
                    stream_deflection_method = deflection_result[1] if deflection_result else None
                    EMLYMessages.insert_new_message(
                        bot_id=self.bot_id, user_id=self.user_id, session_id=self.session_id, topic=current_topic,
                        message=user_input, role="user", not_useful=False, expanded_query=None, page=initial_state.get("page_id"),
                        channel_id=stream_channel_id,
                    )
                    assistant_message = EMLYMessages.insert_new_message(
                        bot_id=self.bot_id, user_id=self.user_id, session_id=self.session_id, topic=current_topic,
                        message=full_response, role="assistant", not_useful=False, expanded_query=None, page=initial_state.get("page_id"),
                        channel_id=stream_channel_id,
                        model_used=model_used,
                        prompt_tokens=final_telemetry.get("prompt_tokens"),
                        completion_tokens=final_telemetry.get("completion_tokens"),
                        response_time_ms=response_time_ms,
                        citations=citations_json,
                        is_deflected=stream_is_deflected,
                        deflection_method=stream_deflection_method,
                    )
                    if assistant_message:
                        message_id = assistant_message.id
                    else:
                        self.logger.warning("Failed to insert assistant message, message_id will be None.")
                        message_id = None

                yield {"type": "citations", "data": final_citations, "message_id": message_id}

            return stream_generator()

        except Exception as e:
            self.logger.error(f"Error processing workflow: {e}", exc_info=True)
            if stream:
                def on_stream():
                    yield {"type": "token", "data": "I'm sorry, a critical error occurred."}
                    yield {"type": "citations", "data": []}
                return on_stream()
            return "I'm sorry, a critical error occurred.", []

    def _handle_command(self, command: str) -> str:
        """Handles special slash commands."""
        cmd = command.lower().strip()
        if cmd in ["/quit", "/exit"]:
            return "Exiting."
        if cmd == "/help":
            return "Available commands: /quit, /exit, /new, /topic, /slots"
        if cmd == "/new":
            self.conversation_manager.current_topic = None
            self.slot_manager.filled_slots = {topic: {} for topic in self.topics}
            return "Started a new conversation."
        if cmd == "/topic":
            return f"Current topic: {self.conversation_manager.current_topic or 'None'}"
        if cmd == "/slots":
            topic = self.conversation_manager.current_topic
            if topic:
                slots = self.slot_manager.get_filled_slots(topic)
                return f"Filled slots for '{topic}': {json.dumps(slots, indent=2)}"
            return "No active topic to show slots for."
        return f"Unknown command: {command}"

def create_config(bot_id: str) -> Dict[str, Any]:
    """Build the runtime config dict for a single bot.

    Reads ``bots.config_json`` for the given bot (which is in the
    ``BotConfigV1`` shape from ``services/bot_config.py``) and assembles
    the legacy-shaped dict the rest of this module consumes (topics,
    global_prompts, model_name, …).
    """
    try:
        import copy
        from services.bot_config import get_config_for_bot, get_decrypted_api_key
        from utils.utils import process_trigger_prompts

        try:
            bot_config = get_config_for_bot(bot_id).model_dump(mode="json")
        except LookupError:
            bot_config = {}

        # New shape (BotConfigV1) has topics / global_prompts at top
        # level. Legacy shape nested them under "agent_config". Support
        # both for transitional compat: look at the top-level first,
        # fall back to "agent_config" for old config rows.
        config_from_file = copy.deepcopy(
            {
                "topics": bot_config.get("topics", {}),
                "global_prompts": bot_config.get("global_prompts", {}),
            }
            if bot_config.get("topics") or bot_config.get("global_prompts")
            else bot_config.get("agent_config", {})
        )

        # Define defaults
        default_config = {}

        # Merge: file config overrides defaults, defaults fill missing keys
        def deep_merge(default: dict, override: dict) -> dict:
            result = default.copy()
            for k, v in override.items():
                if isinstance(v, dict) and isinstance(result.get(k), dict):
                    result[k] = deep_merge(result[k], v)
                else:
                    result[k] = v
            return result

        config = deep_merge(default_config, config_from_file)

        # LLM model: prefer the bot's per-row override (BotConfigV1.llm.model),
        # fall back to the deployment-wide MODEL env default.
        llm_cfg = bot_config.get("llm") or {}
        config["model_name"] = llm_cfg.get("model") or MODEL
        config["embedding_model_name"] = EMBEDDING_MODEL_NAME

        # All RAG-enabled topics share the bot's single Qdrant tenant.
        # The collection is bot-scoped via payload filter, not per-topic.
        c_forms_selected = bot_config.get("c_forms_selected", [])
        for topic in config.get("topics", {}):
            topic_prompt = config["topics"][topic].get("prompts", {})
            trigger_prompts = process_trigger_prompts(c_forms_selected, topic)
            
            # Get the existing llm_response or start with empty string
            llm_response = topic_prompt.get("llm_response", "")

            llm_response = clean_template_string(llm_response)
            
            # Build the response starting with base prompt
            parts = [llm_response] if llm_response else []
            
            # Add Context if not present
            if "{context}" not in llm_response:
                parts.extend(["### Context starts: ###", "{context}", "### Context ends. ###"])
            
            # Add User Request if not present (emphasize this)
            if "{user_input}" not in llm_response:
                parts.extend(["### User Request starts: ###", "{user_input}", "### User Request ends. ###"])
            
            # Add trigger prompts as conditional special instructions at the end
            if trigger_prompts and trigger_prompts.strip():
                parts.extend([
                    "",
                    "### Special Instruction: ###",
                    trigger_prompts,
                    "### End Special Instructions ###"
                ])
            
            # Handle slots
            slots = config["topics"][topic].get("slots", [])
            slots = [{**slot, "required": True} for slot in slots]
            
            # Add Preferences/filled_slots if slots exist and not already present
            if slots and "{filled_slots}" not in llm_response:
                parts.extend(["### Preferences starts: ###", "{filled_slots}", "### Preferences ends. ###"])
            
            # Add History if not present
            if "{history}" not in llm_response:
                parts.extend(["### History starts: ###", "{history}", "### History ends. ###"])
            
            
            # Join all parts
            topic_prompt["llm_response"] = "\n".join(parts)
            config["topics"][topic]["prompts"] = topic_prompt
        
        global_prompts = config.get("global_prompts", {
            "welcome_message": "🤖 Hello! I'm a re-engineered AI assistant. How can I assist you today?",
            "goodbye_message": "🤖 Farewell! It was a pleasure assisting you.",
            "slot_question": "Please provide {slot_name}?",
            "error_message": "I'm sorry, I encountered an error. Could you please try again?"
        })
        global_prompts["slot_question"] = "Please provide {slot_name}?"
        config["global_prompts"] = global_prompts

        return config

    except Exception:
        log.exception("Error creating config")
        raise


# --- Main Execution ---
@dataclass
class _BotRuntime:
    """Per-bot cached runtime — built lazily on first chat request,
    rebuilt by ``invalidate_bot`` after a config save."""

    config: Dict[str, Any]
    llm: ChatOpenAI
    topics: Dict[str, TopicConfig]
    prompt_manager: PromptManager
    intent_router: IntentRouter
    rag_manager: RAGManager


class ConversationSessionManager:
    """Multi-tenant session registry (Tier 1 / Phase 3 of multi-bot-plan).

    Each bot has its own ``_BotRuntime`` (LLM client / topics / prompt
    manager) cached by ``bot_id``; sessions are keyed
    ``{bot_id}_{user_id}_{session_id}``. Building a runtime hits the
    bots row + decrypts the api_key — done once per bot, then reused.
    """

    def __init__(self, session_ttl: int = 3600, max_sessions: int = 1000):
        self.sessions: TTLCache = TTLCache(maxsize=max_sessions, ttl=session_ttl)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._bot_runtimes: dict[str, _BotRuntime] = {}
        self.embeddings = EMBEDDING_MODEL_INSTANCE
        self.logger = logging.getLogger(__name__)

    def _build_runtime(self, bot_id: str) -> _BotRuntime:
        from services.bot_config import get_decrypted_api_key

        config = create_config(bot_id=bot_id)
        api_key = get_decrypted_api_key(bot_id) or OPENAI_API_KEY
        llm_kwargs = {
            "model": config.get("model_name", "gpt-4o-mini"),
            "temperature": TEMPERATURE,
            "timeout": 30,
            "max_retries": 2,
            # OpenAI emits token usage on the final stream chunk only when
            # this is set. Phase 2 of the backend backfill enables it so
            # streaming-mode telemetry capture is unblocked when we wire
            # it. (Non-streaming pulls usage from the AIMessage directly.)
            "stream_options": {"include_usage": True},
        }
        if api_key:
            llm_kwargs["api_key"] = api_key
        if OPENAI_BASE_URL:
            llm_kwargs["base_url"] = OPENAI_BASE_URL
        llm = ChatOpenAI(**llm_kwargs)
        topics = {
            t_name: TopicConfig(**t_config)
            for t_name, t_config in config.get("topics", {}).items()
        }
        prompt_manager = PromptManager(config.get("global_prompts", {}), topics)
        intent_router = IntentRouter(llm, topics)
        # RAGManager wraps the process-shared Qdrant client and takes
        # bot_id at search/upsert time. Constructed per-bot so the
        # ``self.bot_id`` baked in matches.
        rag_manager = RAGManager(topics=topics, bot_id=bot_id)
        self.logger.info("Built runtime for bot=%s with %d topic(s)", bot_id, len(topics))
        return _BotRuntime(
            config=config,
            llm=llm,
            topics=topics,
            prompt_manager=prompt_manager,
            intent_router=intent_router,
            rag_manager=rag_manager,
        )

    def _runtime_for(self, bot_id: str) -> _BotRuntime:
        rt = self._bot_runtimes.get(bot_id)
        if rt is None:
            rt = self._build_runtime(bot_id)
            self._bot_runtimes[bot_id] = rt
        return rt

    def invalidate_bot(self, bot_id: str) -> None:
        """Drop cached state for a bot; the next request rebuilds."""
        self.logger.info("Invalidating runtime for bot=%s", bot_id)
        self._bot_runtimes.pop(bot_id, None)
        prefix = f"{bot_id}_"
        for key in list(self.sessions.keys()):
            if key.startswith(prefix):
                del self.sessions[key]
        for key in list(self._session_locks.keys()):
            if key.startswith(prefix):
                del self._session_locks[key]

    def session_lock(self, session_key: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_key] = lock
        return lock

    def _get_session_key(self, bot_id: str, user_id: str, session_id: str) -> str:
        return f"{bot_id}_{user_id}_{session_id}"

    def get_handler(
        self, bot_id: str, user_id: str, session_id: str
    ) -> AgenticConversationHandler:
        rt = self._runtime_for(bot_id)
        session_key = self._get_session_key(bot_id, user_id, session_id)

        # Replace any older session for the same (bot, user) pair —
        # one active session per user-on-bot.
        user_prefix = f"{bot_id}_{user_id}_"
        for key in list(self.sessions.keys()):
            if key.startswith(user_prefix) and key != session_key:
                self.logger.info("Evicting stale session %s", key)
                del self.sessions[key]
                self._session_locks.pop(key, None)

        if session_key not in self.sessions:
            self.logger.info(
                "Creating handler bot=%s user=%s session=%s",
                bot_id,
                user_id,
                session_id,
            )
            self.sessions[session_key] = AgenticConversationHandler(
                config=rt.config,
                user_id=user_id,
                session_id=session_id,
                llm=rt.llm,
                embeddings=self.embeddings,
                topics=rt.topics,
                prompt_manager=rt.prompt_manager,
                intent_router=rt.intent_router,
                rag_manager=rt.rag_manager,
                bot_id=bot_id,
            )

        return self.sessions[session_key]

    def clear_session(self, bot_id: str, user_id: str, session_id: str):
        session_key = self._get_session_key(bot_id, user_id, session_id)
        if session_key in self.sessions:
            del self.sessions[session_key]
            self._session_locks.pop(session_key, None)

def setup_agent() -> ConversationSessionManager:
    """Build the per-process session manager.

    Phase 3: API keys are passed directly to ``ChatOpenAI(api_key=...)``
    via the bot's config row — *never* via ``os.environ`` mutation. With
    multiple bots in one process that pattern would race; the bot's key
    needs to belong to the bot, not the worker.
    """
    return ConversationSessionManager()