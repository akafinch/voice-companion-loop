"""A persona as the character page builds one: a few traits, composed into the text the chat model
receives. The loop only ever sees that text, never the traits."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Traits:
    name: str
    backstory: str  # whole sentences, spoken to the character: "You keep a small inn…"
    temperament: str  # completes "You are…"
    way_of_speaking: str  # completes "You speak…"


def compose(traits: Traits) -> str:
    """The persona text. Every request to the chat model follows it with the bot's own speaking
    rules (character.system_prompt), so a persona says who to be, never how to talk."""
    return (
        f"You are {traits.name}. {traits.backstory} You are {traits.temperament}. "
        f"You speak {traits.way_of_speaking}."
    )
