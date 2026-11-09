# Voice Companion Loop

A live, in-character voice conversation on Akamai GPUs: the browser streams speech over WebRTC, an RTX 4000 Ada transcribes it, vLLM on an RTX PRO 6000 Blackwell answers in character, and VoiceStudio speaks the reply in a custom voice — all of it running on LKE, Akamai's managed Kubernetes.

## Run it locally

The bot runs on your machine and calls the three model services wherever they run, such as on the
deployed cluster. You need Node 24, [uv](https://docs.astral.sh/uv/) and a browser with a microphone.

1. Build the talk page:

   ```bash
   cd web && npm ci && npm run build && cd ..
   ```

2. Point the bot at the services:

   ```bash
   export COMPANION_CHAT_URL=http://localhost:8000/v1   # vLLM serving the chat model
   export COMPANION_CHAT_MODEL=<the chat model's name, as vLLM serves it>
   export COMPANION_STT_URL=http://localhost:8001/v1    # vLLM serving transcription
   export COMPANION_STT_MODEL=<the transcription model's name, as vLLM serves it>
   export COMPANION_TTS_URL=http://localhost:3900       # VoiceStudio
   export COMPANION_TTS_KEY=<VoiceStudio's API key>
   ```

3. Start the bot:

   ```bash
   uv run uvicorn companion.app:app --port 8080
   ```

4. Open http://localhost:8080, press Talk and allow the microphone.

Without the relay's two variables, `COMPANION_TURN_AUTH_URL` and `COMPANION_TURN_TLS_HOST`, the
browser connects to the bot directly.

The talk page loads Daily's microphone handler from `c.daily.co` at runtime, so the network has to reach it.

## Character page

The companion's character is two inputs, swappable mid-call: a persona, the text the chat model
receives ahead of every reply, and a voice saved in VoiceStudio. To make a new one, open
http://localhost:8080/character.html, which the build in step 1 makes too.

1. Start from a ready-made character and change its name, backstory, temperament and way of
   speaking. The page shows the persona they compose.
2. Record the speaker reading the line shown, or upload a clean clip of 3 to 30 seconds and write
   down what it says, word for word. Confirm the speaker's permission, then press Clone: VoiceStudio
   clones the voice from the clip alone, with no training.
3. Press Audition: the chat model writes a greeting in character, and VoiceStudio speaks it in the
   voice. A new voice's first audition is the slowest.
4. Press Apply: the companion's next reply comes as the new character, in its voice, on a call
   already in progress too. Reset brings back the built-in character.

Clone a voice only with its speaker's permission: a voice belongs to the person it comes from. The
page asks you to confirm it, and the bot refuses a clone without it.

Nothing is stored. After the bot restarts the companion is the built-in character again, and a
cloned voice lasts only until VoiceStudio restarts, when the default voice takes its place. If the
browser cannot decode its own recording, the page asks for a file instead. The page's API is in
`companion/studio/`, under `/api/studio`.

## Deploy on LKE

The Terraform in `build/infra/` creates everything that bills: a standard LKE cluster with a node
pool per GPU class, two NodeBalancers and an edge firewall. It also installs the cluster's add-ons
and this repository's Helm chart, `deploy/chart`. GPU nodes bill by the hour from `apply` until
`destroy`. `build/infra/README.md` describes each resource, and `variables.tf` every setting.

What runs where: transcription (Whisper large-v3-turbo on vLLM) has the RTX 4000 Ada to itself. The
chat model (Gemma 4 26B-A4B in NVFP4, on vLLM) and speech synthesis (VoxCPM2 in VoiceStudio) share
the RTX PRO 6000 Blackwell, which the NVIDIA device plugin advertises as two GPUs. Time-slicing
divides no memory, so the chat model is capped at 40% of the card's, which leaves speech synthesis
the rest. Each model takes several minutes to load. Measured on standard LKE in Seattle on
27 October 2026, on one RTX 4000 Ada and one RTX PRO 6000 Blackwell node; confirm in your own
account, and re-measure before relying on the timings.

You need an Akamai account offering both GPU classes as LKE node pools in one region, Terraform 1.5
or later, `kubectl`, and [`linode-cli`](https://github.com/linode/linode-cli) for the teardown check.
Before you apply, `uv run pytest` renders every chart with the values Terraform passes and checks
what must hold on LKE, such as that no Service creates a NodeBalancer Terraform does not own. It
needs [Helm](https://helm.sh/) and the network.

1. **Publish the images.** Push this repository to GitHub. It includes a workflow file, so a push
   over HTTPS needs a token allowed to write workflows. That means a classic token with the
   `workflow` scope, or a fine-grained token with the Workflows permission. Without one, GitHub
   rejects the whole push. A push over SSH needs neither. On every push to `main`, the `images`
   workflow (`.github/workflows/images.yml`) publishes `ghcr.io/<owner>/voice-companion-bot` and
   `ghcr.io/<owner>/voice-companion-speech`, each tagged with the commit. GHCR makes a new package
   private, and the chart pulls without credentials, so make both packages public in their settings
   on GitHub. That cannot be undone.

2. **Configure.** Copy `build/infra/terraform.tfvars.example` to `build/infra/terraform.tfvars`
   and fill it in: the Blackwell plan's id, the addresses allowed in (`allowed_cidrs`, your own
   address as `/32`), an email for Let's Encrypt, and both images with the commit's tag. Region,
   plans and node counts are variables too. Git ignores every `*.tfvars` file in `build/infra/`,
   so your values stay on your machine, and only the template reaches the repository.

3. **Apply.**

   ```bash
   export LINODE_TOKEN=<an Akamai Cloud API token>   # it never enters the cluster
   terraform -chdir=build/infra init
   terraform -chdir=build/infra apply
   ```

4. **Watch it come up.** In Cloud Manager, the cluster, its three node pools and the two
   NodeBalancers appear. In kubectl:

   ```bash
   mkdir -p ~/.kube  # outside the repository: the kubeconfig is a credential
   terraform -chdir=build/infra output -raw kubeconfig | base64 -d > ~/.kube/voice-companion.yaml
   export KUBECONFIG=~/.kube/voice-companion.yaml
   kubectl get nodes -L gpu-class --watch        # the system nodes, then both GPU pools join
   kubectl get pods -n companion --watch         # each GPU service turns ready once its model loads
   kubectl get nodes -o wide                     # the relay listens on UDP at every node's EXTERNAL-IP
   terraform -chdir=build/infra output relay     # ... on this NodePort, and over TLS on 443
   ```

   The bot turns ready last, once speech synthesis has saved the default voice.

5. **Talk.** Open the URL from `terraform -chdir=build/infra output -raw url` at an allowed address,
   press Talk and allow the microphone. Its certificate is Let's Encrypt's, for the page
   NodeBalancer's own hostname, so no domain is needed.

6. **Tear down.** Note the cluster's id, destroy, then check that nothing is left billing:

   ```bash
   terraform -chdir=build/infra output cluster   # its id
   terraform -chdir=build/infra destroy
   deploy/teardown-check.sh voice-companion <cluster id>
   ```

   The check uses `linode-cli`, which reads `LINODE_CLI_TOKEN` or its own configuration, not
   `LINODE_TOKEN`. LKE removes a cluster's nodes a few minutes after the cluster, so run it again
   if only nodes remain. Three things stay behind, and none bills: the node firewall
   (`lke-<cluster id>`), the issued certificates, and the images on GHCR.

Before you rely on it:

- **GPU availability.** Both GPU classes, and the RTX PRO 6000 Blackwell as an LKE node pool in
  Seattle (`us-sea`), were verified against an Akamai account as of 13 October 2026. Plans and their
  availability vary by account and region, so confirm both in your own account. The Blackwell plan
  is absent from the public plan catalog, so its id has no default: read it from Cloud Manager.
- **Hostnames.** The URL is built from each NodeBalancer's IPv4 address, as
  `<a-b-c-d>.ip.linodeusercontent.com`. Such names were verified to resolve publicly, with no CAA
  record in the way, for Seattle NodeBalancer addresses as of 13 October 2026. Confirm for your
  region.
- **Your own uplink.** The allow-list is the only gate, the character page included. Present over
  your own connection, such as a phone's hotspot, never shared venue Wi-Fi: behind the venue's one
  address, anyone in the room who sees the URL could open it, and clone or apply a voice.
