import logging
import os
import time
from pathlib import Path
from langchain_openai import OpenAIEmbeddings
from sentence_transformers import CrossEncoder
from sentence_transformers import SentenceTransformer
from datetime import timedelta
from zoneinfo import ZoneInfo

from utils import model_pins

# The DB stores naive datetimes and the admin UI reads them as UTC, but many
# writers use ``datetime.now()``. Pin the process clock to UTC so those agree
# on any host (containers already default to UTC; a dev laptop doesn't).
os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()

SRC_LOG_LEVELS = {"DB": logging.INFO, "MODELS": logging.INFO, "ACTIONS": logging.INFO}
BACKEND_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", BACKEND_DIR / "data")).resolve()
QDRANT_DATA_PATH = f"{DATA_DIR}/qdrant_db"
IMPRESSION_USER = "emly-gs-53f89d90-67a0-4326-b55b-3d97178ff454"
TOP_K = int(os.getenv("RAG_TOP_K", 5))
# The chat model / endpoint / key (and their MODEL, OPENAI_BASE_URL,
# OPENAI_API_KEY deployment defaults) are resolved in services/llm_settings.py.
MAX_TOKENS = os.getenv("MAX_TOKENS", 1024)

############# Load chunk size and chunk overlap #############
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", 2048))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", 256))
#############################################################


############# Load LLm and Embedding model configuration #############
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "huggingface")
# Model names, pinned revisions and cache folders: utils/model_pins.py (D2).
SENTENCE_TRANSFORMERS_HOME = model_pins.embedding_cache_dir()
RE_RANKING_MODEL_CACHE_FOLDER = model_pins.rerank_cache_dir()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL") or None

EMBEDDING_MODEL_INSTANCE = None
EMBEDDING_MODEL_NAME = model_pins.embedding_model_name()
if EMBEDDING_PROVIDER == "openai":
    OPENAI_EMBEDDING_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL")
    EMBEDDING_MODEL_NAME = OPENAI_EMBEDDING_MODEL or EMBEDDING_MODEL_NAME
    EMBEDDING_MODEL_INSTANCE = OpenAIEmbeddings(
        api_key=OPENAI_API_KEY,
        model=OPENAI_EMBEDDING_MODEL,
        base_url=OPENAI_BASE_URL,
    )
elif EMBEDDING_PROVIDER == "huggingface":
    try:
        EMBEDDING_MODEL_INSTANCE = SentenceTransformer(
            EMBEDDING_MODEL_NAME,
            cache_folder=SENTENCE_TRANSFORMERS_HOME,
            device="cpu",
            **model_pins.embedding_load_kwargs(),
        )
    except OSError as exc:
        if not model_pins.hf_offline():
            raise
        raise model_pins.missing_model_error("embedding", EMBEDDING_MODEL_NAME, SENTENCE_TRANSFORMERS_HOME, exc) from exc
    class SentenceTransformerEmbeddings:
        def __init__(self, model):
            self.model = model

        def embed_query(self, text: str):
            return self.model.encode(text, normalize_embeddings=True).tolist()

        def embed_documents(self, texts: list[str]):
            return [self.model.encode(t, normalize_embeddings=True).tolist() for t in texts]
        # optional: so your old code that calls .encode still works
        def encode(self, texts, normalize_embeddings=True):
                # If single string, wrap it in list and return the first result
            if isinstance(texts, str):
                return self.model.encode(texts, normalize_embeddings=normalize_embeddings).tolist()
            # If it's a list of strings or list of Documents
            elif isinstance(texts, list):  # Document objects
                texts = [doc.page_content for doc in texts]
                return self.model.encode(texts, normalize_embeddings=normalize_embeddings).tolist()
    EMBEDDING_MODEL_INSTANCE = SentenceTransformerEmbeddings(EMBEDDING_MODEL_INSTANCE)

else:
    raise ValueError(f"Unsupported EMBEDDING_PROVIDER: {EMBEDDING_PROVIDER}")
USE_EMLY_SUMMARIZE = True if os.getenv("USE_EMLY_SUMMARIZE", "true").lower() == "true" else False
##############################################################


############################################################
# LOAD DB Configuration
############################################################
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR}/emlygenai_app.db")


############################################################
SUMMARY_RECOMMENDATION_PROMPT = os.getenv("SUMMARY_RECOMMENDATION_PROMPT", """Understand the conversation between user and assistant.  Assistant represents a business on behalf of which it is having conversation with the user and providing answers to the users queries based on the business's knowledge base.
User can ask for a callback or submit any action form, this triggers an email with form details and raw conversation. This email is sent to a human agent of the business. Now you task to to summarize and analyze  the conversation which will be sent with that email that will help human agent to help user in a better way. Based on the conversation you should coach human agent to perform better.
The summary and analysis should contain


        1. CONVERSATION SUMMARY
        - Most important theme of conversation
        - Main questions or concerns raised
  - Highlight the question which were not answered by the assistant


        - Important decisions or conclusions reached
        - Any unresolved issues
	 2. Sentiment of user during conversation
        3. Finally NEXT STEPS RECOMMENDATION for the human agent

        Format the response in HTML markdown using the following structure:
        <div class="analysis-container">
            <h2>Conversation Summary</h2>
            <!-- Summary content -->

            <h2>Recommended Next Steps</h2>
            <!-- Recommendations content -->
        </div>

        Important: Provide ONLY the HTML content without any markdown code fences or additional formatting. Start directly with <div> and end with </div>""")
#############################################################

############ Load previous message configuration #############
LATEST_N_MESSAGES = int(os.getenv("LATEST_N_MESSAGES", 3))
NUMBER_OF_LAST_MESSAGES = os.getenv("MAX_MESSAGE_CONTEXT", 5)
# A messaging-channel conversation (Slack DM, Telegram chat, ...) starts a
# new session after this much silence, or as soon as it is resolved.
CHANNEL_SESSION_IDLE_HOURS = float(os.getenv("CHANNEL_SESSION_IDLE_HOURS", 24))
############################################################

####################################
# Load Email settings
####################################
# No default relay: when EMAIL_SERVER is unset, email features (OTP, invites,
# form alerts) are disabled rather than sending through a third party.
EMAIL_SERVER = (os.getenv("EMAIL_SERVER") or "").strip() or None
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 465))
EMAIL_USER = os.getenv("EMAIL_USER", "resend")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_DELAY_IN_MINUTES =  int(os.getenv("EMAIL_DELAY_IN_MINUTES", 5))
PROCESS_POOL_SIZE = int(os.getenv("PROCESS_POOL_SIZE", 4))
MAX_JOB_INSTANCES = int(os.getenv("MAX_JOB_INSTANCES", 4))

####################################
# Load otp settings
####################################
FILE_NAME = "email_otp_verification_email.html"
UTILS_DIR = Path(BACKEND_DIR / "utils").resolve()
with open(UTILS_DIR / FILE_NAME, "r") as f:
    EMAIL_OTP_TEMPLATE = f.read()

####################################
# Load Reranking model
####################################
ENABLE_RAG_HYBRID_SEARCH = os.environ.get("ENABLE_RAG_HYBRID_SEARCH", "false").lower() == "true"
RE_RANKING_MODEL = model_pins.rerank_model_name()
if ENABLE_RAG_HYBRID_SEARCH:
    try:
        RE_RANKING_MODEL = CrossEncoder(RE_RANKING_MODEL, max_length=512, cache_folder=RE_RANKING_MODEL_CACHE_FOLDER,
                                        **model_pins.rerank_load_kwargs())
    except OSError as exc:
        if not model_pins.hf_offline():
            raise
        raise model_pins.missing_model_error("re-ranking", RE_RANKING_MODEL, RE_RANKING_MODEL_CACHE_FOLDER, exc) from exc


SESSION_TIMEOUT = timedelta(minutes=30)

############################################
# Prompt related settings
############################################
INTENT_DETECTION_PROMPT = os.getenv("INTENT_DETECTION_PROMPT", "You are an expert at routing user requests. Based on the conversation so far and the user's input, pick the most relevant topic.")


DEFAULT_MESSAGE  = os.getenv("DEFAULT_MESSAGE", "Unfortunately, I cannot find this information. Please try a different query.")
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))
TEMPERATURE = float(os.getenv("TEMPERATURE", 0.7))


####################################
# Qdrant client (vector store)
####################################
# Embedded mode for local dev (no Docker), server mode in prod via QDRANT_URL.
# Single shared collection `bots`; multi-tenancy is via the bot_id payload index.
# All access goes through agents.rag_manager.RAGManager — never use this client
# directly outside that module.
####################################
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "bots")
