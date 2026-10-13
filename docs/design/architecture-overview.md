# Voice Companion Loop: architecture overview

**Status:** the reviewed design as of 2026-10-13, before it is grounded in concrete infrastructure.
Specific engines, sizes and versions are still being chosen; [Still being settled](#still-being-settled)
lists them. Why the demo runs this way on LKE, and the terms used here, are covered in
[compute-platform-options.md](compute-platform-options.md).

## What it is

The Voice Companion Loop is a live, spoken conversation with an AI character. You talk in a browser;
the companion hears you, answers in character, and speaks the answer in its own voice, turn after turn.
A second page reshapes the character: a persona composed from a few characteristics, and a voice cloned
from a few seconds of speech. Apply it mid-conversation, and the next reply comes from the new character,
on the same call, with the conversation so far intact.

Everything runs on Akamai Cloud: open models on Akamai GPUs, on standard LKE, brought up and torn down by
Terraform. The design has three parts, each something the reader does:

| Part | The reader... | What it shows |
|---|---|---|
| **Talk to the companion** | talks in the browser and hears each reply, with every stage's timing on screen | real-time voice over WebRTC; open models hearing, answering and speaking fast enough to converse; a character kept in context |
| **Give it a character** | reshapes the persona and clones a voice on a separate page, auditions the result, and applies it | a character is two swappable inputs, an instruction and a voice, added with no new GPU or service |
| **Deploy on LKE** | runs Terraform, watches the GPU pools and the relay come up, talks at the printed URL, and tears it all down | a GPU application on standard LKE, real-time audio through a relay, and teardown to zero |

## The pieces

```mermaid
flowchart LR
    subgraph user["Browser, from an allow-listed address"]
        talk["Talk page"]
        charpage["Character page"]
    end

    subgraph cloud["Akamai Cloud: one region"]
        nbPage["Page NodeBalancer<br/>HTTPS on 443"]
        nbRelay["Relay NodeBalancer<br/>TURN over TLS on 443"]
        subgraph lke["Standard LKE cluster"]
            front["HTTPS front"]
            bot["Bot: one process<br/>pages, signalling, character API,<br/>voice pipeline, active character"]
            relay["TURN relay (STUNner)"]
            subgraph speech["Speech pool: RTX 4000 Ada"]
                stt["Transcription"]
                tts["Speech synthesis and<br/>voice cloning (VoiceStudio)"]
            end
            subgraph language["Language pool: RTX PRO 6000 Blackwell"]
                llm["Language model (vLLM)"]
            end
        end
    end

    talk -->|HTTPS| nbPage
    charpage -->|HTTPS| nbPage
    nbPage --> front --> bot
    talk -. "WebRTC audio over UDP,<br/>to a NodePort on any node" .-> relay
    talk -. "or TURN over TLS" .-> nbRelay -.-> relay
    relay -. "to the bot's pod address" .-> bot
    bot -->|transcribe| stt
    bot -->|"reply, audition greeting"| llm
    bot -->|"speak, clone"| tts
```

- **One web origin.** Both pages, each call's signalling and connection settings, and the character API
  all come from one bot process behind one HTTPS address, so nothing crosses origins. The HTTPS front
  holds the page's certificate.
- **One GPU pool per GPU class.** Transcription and speech synthesis run on the RTX 4000 Ada pool, and
  the language model on the RTX PRO 6000 Blackwell pool. Each service is reachable only inside the
  cluster, and counts as ready only once its model has loaded, so "up" means "answers". The bot itself
  needs no GPU.
- **Swappable engines.** The bot reaches all three GPU services through OpenAI-compatible HTTP
  interfaces, so any one of them can change without touching the rest.
- **Allow-listed access.** Cloud Firewalls admit only listed addresses, so a URL shown on a projector
  reaches nothing from the audience's phones. That holds only over the presenter's own uplink: on shared
  venue Wi-Fi, the whole room arrives from the same address.

## How audio reaches the bot

Real-time audio in a browser uses WebRTC, which carries sound over UDP and needs a direct path to the
server. Standard LKE has no UDP load balancer, and the bot's WebRTC library can announce only its private
pod address. So the browser never reaches the bot directly. It relays through a TURN relay inside the
cluster:

- Where UDP is open, the browser reaches the relay on a NodePort, at any node's public address.
- Where a network blocks UDP, it reaches the relay over TLS on port 443, through the relay's own
  NodeBalancer.
- The relay reaches the bot at its pod address, inside the cluster. The bot itself is given no STUN or
  TURN server.
- A node's address may change when LKE replaces the node, so the bot hands the page fresh relay
  settings on every call: the current addresses, the TLS hostname, and short-lived credentials.

## One spoken turn

```mermaid
sequenceDiagram
    participant B as Browser
    participant Bot as Bot
    participant S as Speech services
    participant L as Language model
    Note over B,Bot: Connecting
    B->>Bot: ask for this call's relay settings (HTTPS)
    Bot-->>B: relay addresses, TLS hostname, short-lived credentials
    B->>Bot: open the call through the relay (WebRTC)
    Note over B,L: Every turn
    B->>Bot: the reader speaks
    Bot->>Bot: voice-activity detection ends the turn
    Bot->>S: transcribe the utterance
    S-->>Bot: text
    Bot->>L: persona, speaking rules and the conversation so far
    L-->>Bot: the reply, streamed
    loop each finished sentence
        Bot->>S: speak it in the active voice
        S-->>Bot: audio
        Bot->>B: the companion speaks
    end
    Bot-->>B: transcript, reply and stage timings, on the same connection
```

- **The model remembers nothing.** Every turn, the bot resends the persona, its own speaking rules (short
  plain sentences, no markup, never out of character) and the whole conversation so far. The character
  lives in that context, not in the model.
- **Speed is shown, not claimed.** The page shows the live transcript and how long each stage took to its
  first output. The target is a spoken reply within about two seconds. Short first sentences help,
  because speech starts only once the first sentence is complete.
- **Talking over the companion interrupts it**, as in a real conversation. On stage, a close-talk
  microphone stops the companion's own voice from interrupting it.

## Changing the character

- **The seam.** A character is two things: persona text, and the handle of a voice saved in the speech
  service. The bot holds the active character in memory and reads it before every reply. This seam is
  the only thing the character page and the talk loop share.
- **Composing.** The character page builds the persona from a few named characteristics, starting from
  presets, and shows the exact text the model will receive. The voice is cloned from a few seconds of
  recorded or uploaded speech, with no training run, and only once the speaker's consent is confirmed.
- **Auditioning.** The language model writes a short in-character greeting, and the speech service
  speaks it in the new voice. It plays as a clip over HTTPS.
- **Applying.** The next reply on the talk page comes from the new character, on the same call, with
  the earlier turns kept. Reset restores the built-in character: a distinctive, customer-safe persona
  with a licence-clear voice.
- **Nothing is persisted.** A restart returns the built-in character. No storage volume is used, because
  a volume would outlive the cluster and keep billing.

## Bring-up and teardown

```mermaid
flowchart LR
    vars["Terraform variables:<br/>region, node sizes,<br/>allowed addresses"] --> apply["terraform apply"]
    apply --> billable["Everything that bills:<br/>cluster, two GPU pools,<br/>Cloud Firewalls, two NodeBalancers"]
    apply --> secrets["Generated secrets become<br/>cluster Secrets"]
    billable --> start["Workloads start;<br/>GPU services fetch model weights"]
    secrets --> start
    start --> ready["Every service answers;<br/>Terraform prints the URL"]
    ready -. "after the session" .-> destroy["terraform destroy"]
    destroy --> zero["Nothing left billing"]
```

- **Terraform owns everything that bills**, both NodeBalancers included. Deleting an LKE cluster on its
  own leaves its NodeBalancers and storage volumes on the account, still billing. So the cluster's
  services adopt Terraform's NodeBalancers instead of creating their own, and nothing claims a volume.
- **No domain is needed.** The page and the relay use their NodeBalancers' provider-assigned hostnames,
  unless the relay's certificate turns out to need one (see below).
- **Configuration flows one way.** Terraform variables and generated secrets become cluster Secrets,
  which pods read at start. Nothing is copied by hand, and the account token never enters the cluster.
- **On stage, the system is already up.** GPU nodes and model loads are too slow to watch, so the
  bring-up time is quoted from a timed rehearsal.

## Still being settled

These are being chosen as the design is grounded in concrete infrastructure:

- **The speech engine.** It must clone a voice from a short sample, under a licence fit for a
  customer-facing demo. VoiceStudio's default engine has non-commercial model weights, so an alternative
  engine that VoiceStudio supports is being evaluated.
- **The language model and the transcription engine.**
- **GPU sharing:** whether transcription and synthesis share one speech GPU, or get one each.
- **Where the bot, relay and HTTPS front run:** a small CPU pool, or room on the speech pool.
- **Relay settings:** whether the relay's own credential service supplies each call's settings, or the
  bot assembles them.
- **The relay's certificate.** A certificate authority checks a name by reaching it, and the relay's
  hostname reaches the relay rather than the HTTPS front. If there is no clean path, the demo may need a
  domain after all.
- **Image publishing:** whether CI publishes the project's image, or the reader builds it.

The platform's own open questions are covered by the
[validation jobs](compute-platform-options.md#validation-jobs).

## Availability caveats

- RTX PRO 6000 Blackwell availability, including as LKE node pools, was verified against one Akamai
  account on 2026-09-29. Confirm it in your own account and region.
- The NodeBalancer hostname behaviour was checked against one NodeBalancer on 2026-10-13. Use the
  hostname your own deployment prints.
