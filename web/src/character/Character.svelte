<script lang="ts">
  import { onMount } from "svelte";
  import { toSample } from "./sample";

  type Traits = { name: string; backstory: string; temperament: string; way_of_speaking: string };
  type Draft = { traits: Traits; persona: string; voice: string | null };

  // A line for the speaker to read, so the transcript matches the recording from the start.
  const LINE =
    "Hello! I'm recording a short sample, so the companion can borrow my voice. " +
    "It takes only a few seconds of speech, and no training at all.";
  const MOST_SECONDS = 20; // a recording stops itself after this long

  let presets = $state<Traits[]>([]);
  let draft = $state<Draft | null>(null);
  let recorder = $state<MediaRecorder | null>(null);
  let sample = $state<{ wav: Blob; seconds: number; url: string } | null>(null);
  let transcript = $state(LINE);
  let consent = $state(false);
  let greeting = $state(""); // the latest audition, as an object URL
  let busy = $state(""); // what the page is waiting on
  let notice = $state("");
  let problem = $state("");
  let saving: Promise<void> = Promise.resolve(); // edits on their way to the API, in order

  onMount(async () => {
    try {
      presets = await (await call("presets")).json();
      draft = await (await call("draft")).json();
    } catch (error) {
      problem = `Could not load the character: ${(error as Error).message}`;
    }
  });

  /** One request to the character API. A refusal throws, with the API's reason. */
  async function call(path: string, init?: RequestInit): Promise<Response> {
    const response = await fetch(`/api/studio/${path}`, init);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `${response.status} ${response.statusText}`);
    }
    return response;
  }

  /** Runs one action on the API once any edit has reached it, showing what is waited on or failed. */
  async function act(doing: string, work: () => Promise<void>) {
    busy = doing;
    notice = "";
    problem = "";
    try {
      await saving;
      await work();
    } catch (error) {
      problem = `${doing} failed: ${(error as Error).message}`;
    } finally {
      busy = "";
    }
  }

  // Each edited trait goes to the API, which composes the persona the chat model will receive.
  function save() {
    const traits = JSON.stringify(draft!.traits);
    saving = saving
      .then(async () => {
        const response = await call("draft", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: traits,
        });
        draft!.persona = (await response.json()).persona;
      })
      .catch((error) => {
        problem = `Could not save the edit: ${(error as Error).message}`;
      });
  }

  function pick(preset: Traits) {
    draft!.traits = { ...preset };
    save();
  }

  async function record() {
    notice = "";
    problem = "";
    try {
      const microphone = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Blob[] = [];
      const recording = new MediaRecorder(microphone); // in the browser's own format
      recording.ondataavailable = (event) => chunks.push(event.data);
      recording.onstop = () => {
        microphone.getTracks().forEach((track) => track.stop());
        recorder = null;
        use(new Blob(chunks, { type: recording.mimeType }), "recording");
      };
      recording.start();
      recorder = recording;
      setTimeout(() => recording.state === "recording" && recording.stop(), MOST_SECONDS * 1000);
    } catch (error) {
      problem = `Could not record: ${(error as Error).message}`;
    }
  }

  function choose(event: Event & { currentTarget: HTMLInputElement }) {
    const file = event.currentTarget.files?.[0];
    if (file) use(file, "file");
  }

  /** Makes a recording or a chosen file the sample to clone from. */
  async function use(audio: Blob, from: "recording" | "file") {
    sample = null;
    consent = false; // each new sample needs its speaker's permission
    notice = "";
    problem = "";
    try {
      const { wav, seconds } = await toSample(audio);
      // VoiceStudio would cut a longer clip to 30 s and then drop its transcript, so refuse it.
      if (seconds < 3 || seconds > 30) {
        problem = `The sample lasts ${seconds.toFixed(1)} s, and must last 3 to 30 s.`;
        return;
      }
      sample = { wav, seconds, url: URL.createObjectURL(wav) };
    } catch {
      problem =
        from === "recording"
          ? "This browser's recording could not be decoded. Upload a sample file instead."
          : "This file could not be decoded. Try a WAV or MP3 file.";
    }
  }

  function clone() {
    const form = new FormData();
    form.append("sample", sample!.wav, "sample.wav");
    form.append("transcript", transcript);
    form.append("consent", String(consent));
    act("Cloning", async () => {
      const cloned: Draft = await (await call("voice", { method: "POST", body: form })).json();
      draft!.voice = cloned.voice;
      notice = "Cloned: the draft speaks in this voice now. Audition it.";
    });
  }

  function audition() {
    // A new voice's first audition can take a quarter of a minute: the speech engine recompiles.
    act("Auditioning", async () => {
      const clip = await (await call("audition", { method: "POST" })).blob();
      URL.revokeObjectURL(greeting);
      greeting = URL.createObjectURL(clip);
    });
  }

  function apply() {
    act("Applying", async () => {
      await call("apply", { method: "POST" });
      notice = `Applied: the companion's next reply on the talk page comes as ${draft!.traits.name}, in this voice.`;
    });
  }

  function reset() {
    act("Resetting", async () => {
      await call("reset", { method: "POST" });
      notice = "Reset: the companion's next reply comes as its built-in character.";
    });
  }
</script>

<main>
  <h1>Give the companion a character</h1>
  <p>Shape a character, hear it audition, then apply it to the call on the <a href="/">talk page</a>.</p>

  {#if draft}
    <section>
      <h2>Persona</h2>
      <p>
        Start from
        {#each presets as preset}
          <button onclick={() => pick(preset)}>{preset.name}</button>
        {/each}
      </p>
      <label>Name <input bind:value={draft.traits.name} onchange={save} /></label>
      <label>Backstory <textarea rows="2" bind:value={draft.traits.backstory} onchange={save}></textarea></label>
      <label>Temperament: “You are…” <input bind:value={draft.traits.temperament} onchange={save} /></label>
      <label>
        Way of speaking: “You speak…” <input bind:value={draft.traits.way_of_speaking} onchange={save} />
      </label>
      <p class="persona">
        <span>The persona the chat model receives, before the bot's own speaking rules</span>
        {draft.persona}
      </p>
    </section>

    <section>
      <h2>Voice</h2>
      <p>
        Record the speaker reading this line, or upload a clean clip of 3 to 30 seconds and write what
        it says. The transcript must match the clip word for word.
      </p>
      <textarea rows="3" bind:value={transcript} aria-label="Transcript"></textarea>
      <p>
        {#if recorder}
          <button onclick={() => recorder?.stop()}>Stop</button> Recording, for {MOST_SECONDS} s at most…
        {:else}
          <button onclick={record}>Record</button> or upload
          <input type="file" accept="audio/*" onchange={choose} />
        {/if}
      </p>
      {#if sample}
        <p><audio controls src={sample.url}></audio> {sample.seconds.toFixed(1)} s</p>
      {/if}
      <label class="consent">
        <input type="checkbox" bind:checked={consent} /> The speaker has given their permission to clone their voice.
      </label>
      <button onclick={clone} disabled={!sample || !consent || !transcript.trim() || !!busy}>Clone</button>
      <p>Voice handle: <code>{draft.voice ?? "none yet, so the default voice"}</code></p>
    </section>

    <section>
      <h2>Audition and apply</h2>
      <button onclick={audition} disabled={!!busy}>Audition</button>
      <button onclick={apply} disabled={!!busy}>Apply to the call</button>
      <button onclick={reset} disabled={!!busy}>Reset to the built-in character</button>
      {#if greeting}<p><audio controls autoplay src={greeting}></audio></p>{/if}
    </section>
  {/if}

  {#if busy}<p>{busy}…</p>{/if}
  {#if notice}<p class="notice">{notice}</p>{/if}
  {#if problem}<p class="problem">{problem}</p>{/if}
</main>

<style>
  main {
    max-width: 40rem;
    margin: 2rem auto;
    padding: 0 1rem;
    font-family: system-ui, sans-serif;
  }
  section {
    margin: 2rem 0;
  }
  label {
    display: block;
    margin: 0.75rem 0;
  }
  input:not([type]),
  textarea {
    display: block;
    box-sizing: border-box;
    width: 100%;
    margin-top: 0.25rem;
    font: inherit;
  }
  button {
    font-size: 1rem;
    padding: 0.4rem 1rem;
  }
  .persona {
    padding: 0.6rem 0.9rem;
    border-radius: 0.6rem;
    background: #eef2f7;
  }
  .persona span {
    display: block;
    margin-bottom: 0.3rem;
    font-size: 0.85rem;
    color: #555;
  }
  .notice {
    color: #1b5e20;
  }
  .problem {
    color: #b00020;
  }
</style>
