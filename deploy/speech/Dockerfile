# The speech service: VoiceStudio 0.5.6 with VoxCPM2 installed into its own Python environment.
# VoiceStudio's published image clones voices only with OmniVoice, whose weights are non-commercial;
# VoxCPM2's are Apache-2.0. The base already has the C compiler torch.compile needs, uv, and the
# torch 2.8 pins in /app/deploy/torch-constraints.txt.
# https://github.com/debpalash/VoiceStudio/blob/v0.5.6/deploy/Dockerfile
# https://github.com/debpalash/VoiceStudio/blob/v0.5.6/docs/engines/voxcpm2.md
FROM ghcr.io/debpalash/omnivoice-studio:0.5.6

# Held to the base's torch 2.8: torchcodec 0.7 is the release built for it, and voxcpm leaves
# torchcodec unpinned (https://github.com/pytorch/torchcodec#installing-torchcodec). Brotli 1.2 or
# later, because on the older Brotli this image keeps from its PyTorch base, VoxCPM2's weights failed
# to download when this example was tested on LKE on 27 October 2026.
RUN uv pip install --python "$(command -v python3)" --no-cache \
      --constraint /app/deploy/torch-constraints.txt \
      voxcpm==2.0.3 "torchcodec==0.7.*" "brotli>=1.2"

ENV OMNIVOICE_TTS_BACKEND=voxcpm2

# Starts VoiceStudio and warms VoxCPM2 before the pod reports ready.
COPY --chmod=755 entry.sh /app/entry.sh
ENTRYPOINT ["/app/entry.sh"]
