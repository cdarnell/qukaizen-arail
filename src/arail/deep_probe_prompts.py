"""The day-1 prompt set of the deep probe (sprint 2026-10-29-arail-deep-honest, Appendix C).

Five prompts, in this order: a short question; a long prompt as big as a chat
turn with lab context (about 1,933 tokens); the short question again, word for
word; a long prompt with the same lab-context prefix and a different question;
a second short question. The mix matters: the July and August builds fail in
chunked prefill, and one short question repeated could miss a partial fix.

The set is pinned by hash (``PROMPT_SET_SHA256``). A test fails if one character
changes, so the shipped probe is provably the probe that produced DAY1.json and
its results stay comparable with it.
"""
from __future__ import annotations

import hashlib
import json

caps = ("You are the lab's AI engineer. The lab runs locally. Agents: Buddy (lab partner), SRE (crash watcher), "
        "Researcher (autoresearch engine). Surfaces: Dashboard, Chat, Autoresearch, Knowledge Base, Agents. "
        "Answer briefly; cite the note you used. ")
state = ("Live state: tier maximus; mode hybrid; active goal \"reduce validation loss on the tiny-shakespeare char model below 1.45\"; "
         "last experiment #41 lr=3e-4 warmup=200 steps val_loss=1.512; next planned #42 lr=2e-4 warmup=400. ")
notes = []
for k in range(1, 4):
    notes.append(f"[note {k}] " + " ".join(
        f"Observation {k}.{j}: run {40+j} used batch {32*j} and gradient clipping {0.5+j/10:.1f}; loss curve flattened after step {300*j}; "
        f"the researcher suggested trying cosine decay with a floor of {j}e-5 and logging grad-norm every 50 steps."
        for j in range(1, 9)))
LAB = (caps * 6) + "\n\n" + state + "\n\n" + "\n\n".join(notes) + "\n"      # 6,680 chars
S1 = "In two sentences, what does learning-rate warmup do?"
S2 = "Name one risk of evaluating a model on its own training data."
PROMPTS = [["short", S1],
           ["long", LAB + "\n\nQuestion: summarise the lab's current goal in three bullet points."],
           ["short-repeat", S1],
           ["long-shared-prefix", LAB + "\n\nQuestion: which agent should run next, and why? One paragraph."],
           ["short-2", S2]]
PROMPT_SET_SHA256 = hashlib.sha256(
    json.dumps(PROMPTS, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
# == "b5bcb562d908f5d8c843cfe0b0461bfdeb39933339b45e332e91120f7918641e"
