import os
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv
from openai import OpenAI
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

load_dotenv()

### Example Expert Assistant
##
## Barebones chat agent that registers on Agentverse and is callable from ASI:One.
## It prompts the ASI:One model to answer questions on one subject only.
##

# the subject that this assistant is an expert in
subject_matter = "the sun"

client = OpenAI(
    # By default, we are using the ASI:One LLM endpoint and model
    base_url=os.environ["ASI_ONE_BASE_URL"],
    # Get a key at https://asi1.ai/developer and put it in .env as ASI_ONE_API_KEY
    api_key=os.environ["ASI_ONE_API_KEY"],  # required for this example
)

agent = Agent(
    # Its own identity: reusing AGENT_SEED would make this the same agent as
    # the CodeGate gate, and both would fight over port 8001.
    name=os.getenv("HELLO_AGENT_NAME", "dhruv-sun-expert"),
    seed=os.environ.get("HELLO_AGENT_SEED") or os.environ["AGENT_SEED"] + "-hello-example",
    port=int(os.getenv("HELLO_AGENT_PORT", "8003")),
    mailbox=True,
    publish_agent_details=True,
)

# We create a new protocol which is compatible with the chat protocol spec. This ensures
# compatibility between agents
protocol = Protocol(spec=chat_protocol_spec)


# We define the handler for the chat messages that are sent to your agent
@protocol.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage):
    # send the acknowledgement for receiving the message
    await ctx.send(
        sender,
        ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id),
    )

    # collect up all the text chunks
    text = ""
    for item in msg.content:
        if isinstance(item, TextContent):
            text += item.text

    # query the model based on the user question
    response = "I am afraid something went wrong and I am unable to answer your question at the moment"
    try:
        r = client.chat.completions.create(
            model="asi1-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"""
        You are a helpful assistant who only answers questions about {subject_matter}. If the user asks
        about any other topics, you should politely say that you do not know about them.
                """,
                },
                {"role": "user", "content": text},
            ],
            max_tokens=2048,
        )

        response = str(r.choices[0].message.content)
    except Exception:
        ctx.logger.exception("Error querying model")

    # send the response back to the user
    await ctx.send(
        sender,
        ChatMessage(
            timestamp=datetime.now(timezone.utc),
            msg_id=uuid4(),
            content=[
                # we send the contents back in the chat message
                TextContent(type="text", text=response),
                # we also signal that the session is over, this also informs the user that we are not
                # recording any of the previous history of messages.
                EndSessionContent(type="end-session"),
            ],
        ),
    )


@protocol.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    # we are not interested in the acknowledgements for this example, but they can be useful to
    # implement read receipts, for example.
    pass


# attach the protocol to the agent
agent.include(protocol, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
