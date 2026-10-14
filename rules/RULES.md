# Chess rules — development edition

Version: `triad-chess-dev-v1`. Interface: `chess.v1`.

These describe the confirmed design and the behavior implemented by the current
organizer evaluator. Event clocks match the participant rulebook; other
development parameters remain provisional. This document is not an announcement
that official registration or submissions are open.

## Your agent

- Teams have one to five participants. The 24-hour development window is shared
  across Bluff, UNO and Chess. Development and adapter training use your hardware.
- Choose one of the [four allowed GGUF bases](approved-models.json). Its weights remain frozen and
  official serving uses INT4. An adapter is optional, with LoRA rank from 1
  through 32. Organizers must publish tensor shapes, dtype and byte cap before
  official use.
- Supervised fine-tuning and reinforcement learning of the adapter are allowed.
  You choose your datasets. Stockfish may supply training labels or rewards.
- Submit your harness source, Dockerfile, manifest and optional adapter. Your
  container calls the organizer's local inference gateway. RunPod loads
  your frozen approved base and optional adapter into your own inference process
  for each game attempt; same-model opponents receive separate processes.
  Do not bundle an alternative model server
  or select another base in an inference request.
- Use any language or framework. You may write tactical search, planning, memory,
  retrieval and other custom tools, and use general chess rules libraries.
- **The approved LLM selects the final move.** Supply a completed request ID as
  evidence. Your submitted move must match the move decoded from that request
  for the actual current FEN and execution barrier. A fallback move chosen by your
  code after invalid model output is not valid evidence.
- Imported chess engines, engine forks, and copied engine search or evaluation
  code are prohibited during hosted evaluation. The SDK's checker uses a rules
  library and a fake model; it does not grant permission to put an engine in your
  evaluation agent. Evidence matching alone is not proof of tool provenance.
- Framework packages and offline dependencies may be changed freely within the
  published resource and provenance rules. Evaluation has no Internet access.
  Startup must not fetch packages, model weights or remote services.

Policy on externally trained adapter reuse, pre-event preparation, opening books,
tablebases, code attribution and other provenance edge cases requires an
organizer announcement. Do not infer approval from the framework-neutral API.

## Games and clocks

The evaluator supplies full board state, legal moves, initial FEN and UCI move
history. Ordinary chess legality applies, including castling, en passant and
promotion. Use UCI coordinate notation, with a promotion suffix where needed.
The current evaluator automatically ends a game on current-position threefold
repetition or the fifty-move threshold; participants do not send draw claims.
Checkmate takes precedence. Stalemate, insufficient material and the standard
automatic repetition/move-count outcomes are also recognized.

Only the side to move loses clock time. Your tools, inference queueing and
inference execution all consume your turn's clock. A move must arrive strictly
before the deadline. Invalid actions do not reset the clock. Pondering on the
opponent's turn is allowed and is off-clock, but uses the same resource limits
and one-outstanding-request allowance. It never authorizes an off-turn action.

The development evaluator permits three cumulative invalid actions per player
per game; reaching the limit loses the game. The count does not reset after a
legal move. Some stale, duplicate or out-of-turn messages are rejected without
incrementing that counter. Correct using the returned reason and fresh position;
do not repeatedly retransmit an old action. Resignation is supported on your turn.

Organizers preload/warm models before play. An organizer suspension stops both
agents, their clocks and outstanding model work. Resume uses a fresh observation
and execution barrier. Old buffered actions and pre-barrier evidence cannot be
used. A participant harness failure during a running game loses that game.
Organizer failures invalidate or hold the attempt for operator recovery rather
than count as a participant loss. Recovery may replay the affected game while
preserving the match phase and colors; the final recovery policy is pending.

## Tournament

Practice and finals use PvP double elimination. Each bracket match starts with
one chess game. A draw goes directly to one Armageddon game, with colors swapped:
White must win, Black has less time and advances on a draw. Main-game colors are
balanced with seeded random ties. The grand final begins with one bracket match under double elimination. If the previously undefeated finalist wins, it is champion. If it loses, neither finalist has reached two match losses, so play a reset match. The reset uses the deciding game type and time control, with colors swapped from the deciding game. A main-game reset begins with a main game and proceeds to Armageddon on a draw; an Armageddon reset remains Armageddon, with Black retaining draw odds.

Exactly one hosted practice tournament is planned during development. It provides
feedback to the respective teams and does not determine final seeds. Final seeds
are randomized using a published seed. The organizer may adjust concurrent match
capacity. Both player processes are allocated, loaded and warmed before clocks
start. The default is two concurrent matches, using four independent processes
on one RunPod Pod with configurable GPUs. If the measured GPU profile cannot
admit both players, the match waits before its clocks start. Both processes are
destroyed after the attempt.
Final evaluation has a flexible completion window.

Personal Stockfish practice and continuous team ratings are implemented in the
separate `~/stockfishAPI` service. Use the SDK's [practice client/CLI](../docs/practice.md)
with your own local model and a practice team token. Default clocks are 10 minutes
per side without increment, with selectable colour and levels 1–10. Practice
ratings are provisional internal scores and do not affect hosted results.
Event hosting and availability still require organizer deployment.

## Values awaiting official publication

[`development.json`](development.json) records the event clocks: 10+0 for main
games and 5+3 for Armageddon. The [approved model catalog](approved-models.json)
publishes model IDs,
Q4_K_M GGUF revisions and hashes. Organizers must still publish final
context/compute/cache settings, original adapter-training checkpoints, adapter
schema/dtype/cap, container limits, submission cutoff, recovery and
privacy/provenance policies, and the official platform/base-image policy.
Official release remains gated until those parameters and required rehearsals
are approved.
