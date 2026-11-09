<script lang="ts">
  import {
    PipecatClient,
    type BotOutputData,
    type Participant,
    type PipecatMetricsData,
    type TranscriptData,
  } from "@pipecat-ai/client-js";
  import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";

  type Turn = { who: "you" | "companion"; parts: Record<number, string> };

  // The bot's services report their time to first output under their class names.
  const STAGES: Record<string, string> = {
    OpenAISTTService: "Transcription",
    CompanionLLM: "Reply",
    VoiceStudioTTS: "Speech",
  };

  let client: PipecatClient | null = null;
  let status = $state<"idle" | "connecting" | "connected">("idle");
  let problem = $state("");
  let turns = $state<Turn[]>([]);
  let timings = $state<Record<string, number>>({}); // seconds to first output, by stage
  let speaker: HTMLAudioElement;

  async function talk() {
    status = "connecting";
    problem = "";
    turns = [];
    timings = {};
    try {
      // Fetched for every call, never built into the page: the relay's address can change.
      const relay = await fetch("/api/relay");
      if (!relay.ok) throw new Error(`relay settings: ${relay.status}`);
      const iceServers: RTCIceServer[] = await relay.json();

      client = new PipecatClient({
        transport: new SmallWebRTCTransport(),
        enableMic: true,
        enableCam: false,
        callbacks: {
          // Deprecated in this client, but pipecat 1.12's bot sends no user-input message.
          onUserTranscript: heard,
          onBotOutput: said,
          onMetrics: timed,
          onTrackStarted: play,
          onDisconnected: () => {
            status = "idle";
            client = null;
          },
        },
      });
      await client.connect({ webrtcRequestParams: { endpoint: "/api/offer" }, iceConfig: { iceServers } });
      status = "connected";
    } catch (error) {
      problem = `Could not connect: ${error}`;
      await client?.disconnect();
      client = null;
      status = "idle";
    }
  }

  async function hangUp() {
    await client?.disconnect();
  }

  /** Adds text to the latest turn when it is `who`'s, or else starts a new turn. */
  function add(who: Turn["who"], key: number, text: string) {
    if (turns.at(-1)?.who !== who) turns.push({ who, parts: {} });
    turns[turns.length - 1].parts[key] = text;
  }

  function heard(data: TranscriptData) {
    if (data.final) add("you", Date.now(), data.text);
  }

  // The bot reports each sentence twice: as the companion starts to say it, and once it has been
  // said, as "completed" and under an id of its own. The page shows the first.
  function said(data: BotOutputData) {
    if (data.spoken_status === "completed") return;
    add("companion", data.segment_id ?? Date.now(), data.text);
  }

  function timed(data: PipecatMetricsData) {
    for (const { processor, value } of data.ttfb ?? []) {
      const stage = STAGES[processor.split("#")[0]];
      if (stage && value > 0) timings[stage] = value;
    }
  }

  // The client leaves playing the bot to the page. Its own microphone starts a track too, and
  // every unmute starts the bot's again, so only a new remote audio track is attached.
  function play(track: MediaStreamTrack, participant?: Participant) {
    if (participant?.local || track.kind !== "audio") return;
    const playing = speaker.srcObject as MediaStream | null;
    if (playing?.getAudioTracks()[0]?.id === track.id) return;
    speaker.srcObject = new MediaStream([track]);
  }
</script>

<main>
  <h1>Talk to the companion</h1>

  {#if status === "connected"}
    <button onclick={hangUp}>Hang up</button>
  {:else}
    <button onclick={talk} disabled={status === "connecting"}>
      {status === "connecting" ? "Connecting…" : "Talk"}
    </button>
  {/if}
  {#if problem}<p class="problem">{problem}</p>{/if}

  <section class="timings" aria-label="Time to first output, latest turn">
    {#each Object.values(STAGES) as stage}
      <span>{stage} <strong>{timings[stage] ? `${timings[stage].toFixed(2)} s` : "–"}</strong></span>
    {/each}
  </section>

  <ol class="transcript">
    {#each turns as turn}
      <li class={turn.who}><span>{turn.who === "you" ? "You" : "Companion"}</span> {Object.values(turn.parts).join(" ")}</li>
    {/each}
  </ol>

  <audio bind:this={speaker} autoplay></audio>
</main>

<style>
  main {
    max-width: 40rem;
    margin: 2rem auto;
    padding: 0 1rem;
    font-family: system-ui, sans-serif;
  }
  button {
    font-size: 1.25rem;
    padding: 0.6rem 2rem;
  }
  .problem {
    color: #b00020;
  }
  .timings {
    display: flex;
    gap: 1.5rem;
    margin: 1.5rem 0;
    color: #555;
  }
  .transcript {
    list-style: none;
    padding: 0;
  }
  .transcript li {
    margin: 0.75rem 0;
    padding: 0.6rem 0.9rem;
    border-radius: 0.6rem;
    background: #eef2f7;
  }
  .transcript li.companion {
    background: #e8f5e9;
  }
  .transcript span {
    font-weight: 600;
    margin-right: 0.4rem;
  }
</style>
