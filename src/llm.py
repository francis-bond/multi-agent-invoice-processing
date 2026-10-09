"""One place that builds the model client.

Three modules call a model and each had its own copy of this, which meant three places to
change the provider and three chances to forget. It also meant `load_dotenv()` lived in
whichever of them happened to be imported first: approve and critique read XAI_API_KEY but
never loaded the .env file, so they worked only because graph imports extract before them.
Importing approve on its own would have raised KeyError on a correctly configured machine.

Anything that needs a key imports this, so the key is always loaded.
"""

import os

from dotenv import load_dotenv
from langchain_core.runnables import Runnable
from langchain_xai import ChatXAI

load_dotenv()

DEFAULT_MODEL = "grok-4-1-fast"

# Transient failures - a 502, a rate limit, a dropped connection - should not end a run that
# was otherwise fine. Three attempts with backoff.
#
# The cost of putting this around the structured-output call rather than the raw one is that a
# model which genuinely cannot produce the schema burns three attempts instead of one. That is
# the better trade: at temperature 0 a schema failure is rare, while a network blip on a
# twenty-invoice batch is not, and losing a run to one is worse than three wasted calls.
MAX_ATTEMPTS = int(os.environ.get("LLM_MAX_ATTEMPTS", "3"))


def client(schema: type, temperature: float = 0) -> Runnable:
    """A model bound to a Pydantic schema, with retries on transient failure.

    Swapping provider is a change here and nowhere else, which is the point of going through
    LangChain's chat interface rather than an xAI SDK.
    """
    llm = ChatXAI(
        model=os.environ.get("XAI_MODEL", DEFAULT_MODEL),
        api_key=os.environ["XAI_API_KEY"],
        temperature=temperature,
    )
    return llm.with_structured_output(schema).with_retry(stop_after_attempt=MAX_ATTEMPTS)
