# Docker and submission preparation

The evaluator runs your image's `ENTRYPOINT` with stdin attached and stdout piped.
Use exec-form `ENTRYPOINT` so the agent receives lifecycle signals directly. No
interactive terminal is allocated. You may change the language, base image,
packages, source layout and entry point; the [protocol](protocol.md) is the
boundary you must preserve.

## Prepare the Python starter

From the repository root, install your development dependencies as described in
the README. Declare runtime dependencies with `uv add PACKAGE`, then prepare
Linux wheels while you have Internet access. The preparation command exports
`requirements.txt` from `uv.lock`; do not edit that generated file directly:

```sh
uv run --locked --extra dev python scripts/prepare_wheels.py
uv run --locked --extra dev python scripts/prepare_wheels.py --check
```

The first command downloads Python 3.12 Linux x86-64 wheels to `vendor/wheels`.
The second prepares the pure-Python rules-library wheel for the optional checker
image. Both directories contain SHA-256 manifests. Commit the required wheels
and offline assets; the organizer captures a Git commit, not your working tree,
virtual environment or local package cache. The template already includes its
small runtime wheelhouse. Regenerate after changing dependencies.

Change `--platform` and `--python` to match your parent image when needed. Pure
Python wheels work on multiple architectures; native packages must match Linux,
CPU architecture and Python ABI. Packages available only as source require a
separate compatible Linux wheel build or a vendored build toolchain. The script
fails rather than silently download an incompatible source distribution. For
non-Python agents, replace this step with your own offline dependency preparation.

The base image must be preloaded on the build machine. During development:

```sh
docker pull python:3.12-slim-bookworm
docker build --network=none --pull=false -t chess-agent .
```

The pull is preparation, before the offline build. The template installs packages
with `--no-index --find-links`, never from a live registry. Organizers must publish
approved parent images/digests and target architecture before official release.
Replace the template's mutable parent tag with their pinned digest, and test that
image locally. `--network=none` disables networking for build steps; it does not
prevent a missing `FROM` image from being fetched. Preload the parent and keep the
registry unavailable for a fully offline check. See Docker's official
[Dockerfile reference](https://docs.docker.com/reference/dockerfile) and
[build options](https://docs.docker.com/reference/cli/docker/buildx/build/).

The `.dockerignore` excludes development caches, tests, documentation and private
`.env` files. Add ignore patterns for your secrets. Explicitly copy your custom
modules/resources into the image: the template copies `agent.py`, `chess_sdk`
and `assets`. Dependencies must be installed at build time. Runtime compilation,
package installs and executable downloads will not work in the default sandbox.

## Check inside a restricted container

Build the optional target that contains the checker and a fake inference gateway:

```sh
docker build --network=none --pull=false --target local-check -t chess-agent-check .
docker run --rm --network=none --read-only --user=65532:65532 \
  --cap-drop=ALL --security-opt=no-new-privileges:true \
  --cpus=1 --memory=512m --memory-swap=512m --pids-limit=64 --ipc=none \
  --tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777 \
  chess-agent-check
```

Add `--colour black` to check the other color. Both the temporary gateway and the
agent run inside this container. The final default image omits the checker-only
rules dependency. If you replace the entry point, adapt the check target to invoke
`chess-sdk check --command YOUR_EXECUTABLE YOUR_ARGUMENTS`, or use the host checker
against your executable during development.

Docker Desktop on macOS does not turn a host-native Unix socket into a usable
Linux container socket by bind mounting it. The contained fixture avoids that
problem. Official organizer execution mounts its gateway from the same Linux
host. Passing this fixture does not establish model capacity, official deployment
readiness or tool provenance.

## Evaluation runtime

The current development sandbox uses UID/GID 65532, no network, a read-only root
filesystem, dropped capabilities and no privilege escalation. It limits the
container to one CPU, 512 MiB RAM, 64 processes and a writable 64 MiB `/tmp` with
`noexec`. These are provisional event settings. All runtime writes/cache files
must fit `/tmp`; set framework-specific cache paths there. Persisted process
memory lasts for one game, not across games. Total stderr budget is 65,536 bytes;
exceeding it can terminate the harness. Startup and shutdown are bounded.

The organizer mounts only the scoped inference socket directory read-only at
`/run/inference` and injects `CHESS_INFERENCE_SOCKET` and
`CHESS_INFERENCE_TOKEN`. Connecting to the socket remains possible through that
mount. No GPU, base model weights, Docker socket, organizer database, practice
service or other team's files are available. LLM execution happens on RunPod in a separate process for your player
for each game attempt, with your selected base and optional adapter. Even
same-model opponents have separate processes; model files may be cached on disk.
The organizer forwards requests through SSH and does not expose its credentials
to your container. Do not attempt to start Ollama or download a checkpoint inside
your evaluation container.

During suspension the entire container is paused, including tool subprocesses.
Your agent must still handle the suspend/resume messages and fresh barrier after
it is unpaused. Keep stdin responsive, cancel stale jobs and stop on shutdown.

## Capture your submission

Set `model_id` in `submission.json` to `horizon`, `qwen`, `nemotron`, or `gemma`
from [the approved model catalog](../rules/approved-models.json). Keep `adapter:null`
for an unadapted entry, or set it to a captured repository directory such as
`assets/adapter`. The organizer validates and serves that adapter; your agent
need not load it. `harness_config` contains your own JSON configuration and is
passed through initialization. The manifest's only supported keys are `protocol`,
`model_id`, `adapter`, `harness_config`.

The current intake expects `adapter_model.safetensors` and `adapter_config.json`
inside that adapter directory. Its config must declare `peft_type: LORA`, a rank
`r` from 1 through 32, and `base_model_name_or_path` matching the approved
checkpoint. Variable
rank patterns, DoRA and `modules_to_save` are unsupported. Tensor names, shapes,
dtype, finite values and byte size are validated against the organizer's registry.
Those registry values are still pending, so adapter capture is gated until they
are published. An unadapted entry can use the development flow now.

Commit source, the root Dockerfile, manifest, vendored dependencies and assets.
Supply the repository URL and exact 40-character Git commit through the
organizer's announced submission channel. Git LFS pointers, private caches or
ignored/untracked files are not captured file contents; ensure the required
artifacts are present at that commit. Do not embed credentials in repository URLs.
Changes after capture require a new submission before the published cutoff.
Organizers build offline and run an immutable image ID after validation. Adapter
tensor schema, dtype, byte cap, source/artifact quotas and the submission cutoff
are pending. The supported LoRA rank is 1 through 32.


An optional-adapter manifest has the same contract (use an actual published model ID):

```json
{
  "protocol": "chess.v1",
  "model_id": "qwen",
  "adapter": "assets/adapter",
  "harness_config": {"ponder": false}
}
```

Keep the adapter files in that committed directory; the participant harness does
not choose a serving alias or load the adapter during evaluation.

The committed `uv.lock` is the dependency source of truth. Change Python
dependencies with `uv add`, then prepare wheels again. This exports exact runtime
pins, verifies downloaded wheel/source hashes against the lockfile, builds pure
source packages with uv, and vendors pinned uv binaries for ARM64 and AMD64.
Docker installs from that captured wheelhouse using `uv pip install --offline`;
no installer or dependency is fetched during the build. Python remains the
container entrypoint, so the protocol does not depend on invoking a package manager.
