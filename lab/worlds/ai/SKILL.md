---
title: "AI & Machine Learning"
id: world-ai
name: "AI & Machine Learning"
domain: ai
version: "1.0.0"
tags: [world, knowledge, ai]
when_to_use:
  - When the user asks about AI & Machine Learning or its declared categories
  - When grounding a claim that falls inside this World's domain
when_not_to_use:
  - When the question is outside this World's declared categories
  - When a claim cannot be tied to one of this World's sourced terms (say so; don't invent)
---
This lab studies how modern AI systems are built, trained, tuned, quantized, served, and debugged. Every factual claim is grounded in the World's cited sources; the glossary spans fundamentals, architecture, training, fine-tuning, RL & alignment, quantization, inference, performance, formats & runtime, and the training-run clinic (symptoms, conditions, pathologies, remedies).

Some terms are model-asserted (unverified); cite a source when promoting them.

_This World has 339 terms; the 153 most connected are shown here. The full glossary lives in the Knowledge Base._

_Answer only from the terms below. Every term lists its source. If a question cannot be answered from these terms, say the World does not cover it — do not invent._

### Architecture

- **Agent** (`agent`) — An LLM that takes actions — calls tools, makes decisions — toward a goal, not just chats.
  - Source: QuKaiZen AI Dictionary
- **Agentic** (`agentic`) — Software built around autonomous, tool-using model agents.
  - Source: QuKaiZen AI Dictionary
- **Attention** (`attention`) — The mechanism that lets each token weigh and pull information from every other token.
  - Source: authored
- **CoALA** (`coala`) — A framework (Princeton, 2023) organizing language agents into memory modules, an action space, and a decision-making loop.
  - Source: Sumers, Yao, Narasimhan & Griffiths, 'Cognitive Architectures for Language Agents' (2023), arXiv:2309.02427
- **Context Window** (`context-window`) — The maximum number of tokens a model can attend to at once — its working span of input plus output.
  - Source: authored
- **Cross-Attention** (`cross-attention`) — Attention where queries come from one sequence and keys/values from another.
  - Source: authored
- **Desired State** (`desired-state`) — The end state you declare; the system's job is to make reality match it.
  - Source: QuKaiZen AI Dictionary
- **Drift** (`drift`) — When the real state of a system diverges from its declared desired state over time.
  - Source: authored
- **Episodic Memory** (`episodic-memory`) — An agent's memory of specific past experiences — what happened, when, in which session.
  - Source: authored
- **Feed-Forward Network** (`feedforward-network`) — The per-token two-layer MLP in each transformer block, where most parameters and stored knowledge live.
  - Source: authored
- **Function Calling** (`function-calling`) — A structured protocol for a model to request a specific tool with typed arguments.
  - Source: QuKaiZen AI Dictionary
- **GELU** (`gelu`) — A smooth activation function used in transformer feed-forward layers.
  - Source: authored
- **Grounding** (`grounding`) — Connecting an agent's language to the real world via tools, environments, or retrieved facts.
  - Source: authored
- **Grouped-Query Attention** (`grouped-query-attention`) — Share key/value heads across groups of query heads to shrink the KV-cache with little quality loss.
  - Source: authored
- **Knowledge Base** (`knowledge-base`) — An external, queryable store of facts and documents a model retrieves from instead of relying on weights alone.
  - Source: authored
- **Layer normalization** (`layer-normalization`) — Normalizes activations across the feature dimension within each example.
  - Source: Ba et al. — Layer Normalization arXiv:1607.06450; Goodfellow et al. — Deep Learning ch.8
- **LayerNorm** (`layernorm`) — Normalizes activations within each layer to keep training stable; modern LLMs often use RMSNorm.
  - Source: authored
- **Long-Term Memory** (`long-term-memory`) — An agent's durable store that survives across sessions, beyond the context window.
  - Source: authored
- **MoE** (`moe`) — A model split into many expert sub-networks where a router activates only a few per token.
  - Source: authored
- **Multi-Agent** (`multi-agent`) — Several specialized agents collaborating, each owning a function.
  - Source: QuKaiZen AI Dictionary
- **Multi-Head Attention** (`multi-head-attention`) — Run several attention operations in parallel, each in its own subspace, then concatenate.
  - Source: authored
- **Orchestration** (`orchestration`) — Coordinating multiple agents or services into one coherent flow.
  - Source: QuKaiZen AI Dictionary
- **Planning** (`planning`) — An agent breaks a goal into an ordered set of subtasks before (or while) acting.
  - Source: authored
- **Positional Encoding** (`positional-encoding`) — Information added to tokens so the otherwise order-blind transformer knows their sequence positions.
  - Source: authored
- **RAG** (`rag`) — Fetch relevant documents at query time and feed them to the model as context.
  - Source: QuKaiZen AI Dictionary
- **ReAct** (`react`) — An agent pattern that interleaves reasoning steps ('thoughts') with actions ('tool calls') in a loop.
  - Source: authored
- **Reconciliation** (`reconcile`) — Continuously closing the gap between the team you declared and the team that's running.
  - Source: QuKaiZen AI Dictionary
- **Reflection** (`reflection`) — An agent reviews its own past actions or outputs and writes higher-level lessons or corrections.
  - Source: authored
- **Semantic Memory** (`semantic-memory`) — An agent's store of general world knowledge and facts, decoupled from any single experience.
  - Source: authored
- **Sliding-Window Attention** (`sliding-window-attention`) — Each token attends only to a fixed window of nearby tokens, making attention linear in length.
  - Source: authored
- **Tool Use** (`tool-use`) — A model invoking external tools — APIs, code, search — to act beyond text.
  - Source: QuKaiZen AI Dictionary
- **Transformer** (`transformer`) — The attention-based neural architecture behind essentially every modern LLM.
  - Source: authored
- **Transformer Layers** (`transformer-layers`) — Transformer layers are a key component in the architecture of transformer models, consisting of self-attention mechanisms and feed-forward networks that enable these models to process sequential data
  - Source: model:ai-engineer

### Remedies & Care Actions

- **Apply warmup schedule** (`apply-warmup-schedule`) — Ramp the LR from near-zero to peak over N steps before the main schedule.
  - Source: HF Trainer docs (warmup_steps, lr_scheduler_type='cosine_with_restarts'); NVIDIA training guide; OLMo training config
- **Switch optimizer** (`switch-optimizer`) — Change the optimizer (e.g., SGD → Adam, Adam → AdamW) to better fit the problem.
  - Source: AdamW: Loshchilov & Hutter arXiv:1711.05101; HF Trainer docs (optim=adamw_hf); PyTorch optimizer docs

### Model Conditions

- **Data leakage** (`data-leakage`) — Validation/test data has leaked into training — metrics are invalid.
  - Source: Goodfellow et al. — Deep Learning ch.5 (evaluation); HF datasets docs (train/test split)
- **Dead neurons** (`dead-neurons`) — ReLU units stuck at zero — never activate, never learn.
  - Source: Goodfellow et al. — Deep Learning §6.3.1 (ReLU and variants); PyTorch activation docs
- **Internal covariate shift** (`internal-covariate-shift`) — Distribution of layer activations shifts during training, slowing convergence.
  - Source: Ioffe & Szegedy — Batch Normalization arXiv:1502.03167; Goodfellow et al. — Deep Learning §8.7
- **Learning rate too high** (`learning-rate-too-high`) — Peak LR exceeds what the schedule/optimizer can stabilize.
  - Source: HF Trainer docs (lr_scheduler_type, warmup_steps); Goodfellow et al. ch.8; OLMo logbook
- **Learning rate too low** (`learning-rate-too-low`) — LR is so small that the optimizer barely moves — training stalls.
  - Source: HF Trainer docs; Goodfellow et al. — Deep Learning ch.8 (hyperparameter tuning)

### Fine-Tuning

- **Adapters** (`adapters`) — Small trainable modules inserted into a frozen model to add new skills without retraining it.
  - Source: authored
- **Adversarial Swarm** (`adversarial-swarm`) — A loop of agents (interrogate, challenge, evaluate, correct) that hardens a model until it stops breaking.
  - Source: QuKaiZen NUCLEUS_AGENT_PROTOCOL
- **Distillation** (`distillation`) — Transfer a big teacher model's behavior into a small student model.
  - Source: authored
- **Domain Adaptation** (`domain-adaptation`) — Specialize a general model to a target domain, often via continued pretraining on domain text.
  - Source: authored
- **Fine-tune** (`fine-tune`) — Continue training a pretrained model on new data to specialize it for a task or domain.
  - Source: authored
- **LoRA** (`lora`) — Fine-tune a model by training tiny low-rank adapter matrices while the base weights stay frozen.
  - Source: qukaizen/docs/TECHNIQUES.md
- **Model Merging** (`model-merging`) — Combine multiple fine-tuned models into one by arithmetic on their weights, no extra training.
  - Source: authored
- **PEFT** (`peft`) — An umbrella for methods (LoRA, adapters, prefix-tuning) that tune a tiny fraction of parameters.
  - Source: authored
- **QLoRA** (`qlora`) — LoRA on top of a 4-bit quantized base model — fine-tune big models on one consumer GPU.
  - Source: authored
- **RAFT** (`raft`) — Fine-tuning that teaches a model to reason over retrieved docs while ignoring distractors.
  - Source: knowledge_base/wiki/concepts/RAFT.md
- **SCoTD** (`scotd`) — Distill a teacher's step-by-step reasoning into a small model via many symbolic CoT traces.
  - Source: knowledge_base/wiki/concepts/SCoTD.md
- **Self-Distillation** (`self-distillation`) — A model acts as its own teacher — its current outputs become training targets for a refined version of itself.
  - Source: authored
- **Soft Targets** (`soft-targets`) — A teacher's full probability distribution used as the training target, not just the single correct label.
  - Source: authored

### Formats & Runtime

- **GGUF** (`gguf`) — A single-file binary format for quantized models, built for fast local inference (llama.cpp).
  - Source: authored
- **Hugging Face** (`huggingface`) — The hub and libraries (Transformers, Datasets, Hub) that are the de facto registry for open models.
  - Source: authored
- **llama.cpp** (`llama-cpp`) — A lean C/C++ inference engine that runs quantized LLMs efficiently on CPUs, Macs, and modest GPUs.
  - Source: authored
- **PyTorch** (`pytorch`) — The dominant deep-learning framework for research and much production, built on eager Python tensors.
  - Source: authored
- **SafeTensors** (`safetensors`) — A safe, fast, zero-copy tensor file format — the modern replacement for pickle-based checkpoints.
  - Source: authored

### Fundamentals

- **Benchmark** (`benchmark`) — A standardized test set used to measure and compare model capability.
  - Source: QuKaiZen AI Dictionary
- **BERT** (`bert`) — BERT
  - Source: model:ai-engineer
- **Chain-of-Thought** (`chain-of-thought`) — Prompting a model to show its intermediate steps, which sharply improves reasoning.
  - Source: QuKaiZen AI Dictionary
- **Embeddings** (`embeddings`) — Dense numeric vectors representing tokens or text so similar meanings sit close together.
  - Source: authored
- **Faithfulness** (`faithfulness`) — Whether a model's output is actually supported by its inputs or stated reasoning — not just plausible.
  - Source: authored
- **Generalization** (`generalization`) — How well a model performs on new, unseen data rather than the data it trained on.
  - Source: authored
- **Gradient Descent** (`gradient-descent`) — The core optimization: repeatedly step parameters in the direction that most reduces the loss.
  - Source: authored
- **Hallucination** (`hallucination`) — When a model states fluent, confident information that is fabricated or unsupported.
  - Source: authored
- **In-Context Learning** (`in-context-learning`) — A model learns a task from examples in its prompt at inference time, with no weight updates.
  - Source: authored
- **Inference** (`inference`) — Running a trained model to produce outputs — the deployment side, as opposed to training.
  - Source: authored
- **Logits** (`logits`) — The model's raw, unnormalized output scores over the vocabulary, before softmax makes them probabilities.
  - Source: authored
- **Parameter** (`parameter`) — A single learned number in a model; their count (e.g. 7B) is the rough measure of model size.
  - Source: authored
- **Perplexity** (`perplexity`) — A measure of how surprised a model is by text — lower means it predicts the text better.
  - Source: authored
- **Prompt** (`prompt`) — The input text you give a model to steer what it does.
  - Source: QuKaiZen AI Dictionary
- **Reasoning** (`reasoning`) — A model working through a problem in intermediate steps instead of answering in one leap.
  - Source: QuKaiZen AI Dictionary
- **Softmax** (`softmax`) — Turns a vector of logits into a probability distribution that sums to 1.
  - Source: authored
- **Tokenizer** (`tokenizer`) — Splits text into tokens (subword units) the model actually reads, and back again.
  - Source: authored

### Inference

- **Beam Search** (`beam-search`) — A decoding strategy that keeps the top-k partial sequences each step to find a higher-probability output.
  - Source: authored
- **Decode Phase** (`decode-phase`) — The token-by-token generation phase, bottlenecked by memory bandwidth rather than compute.
  - Source: authored
- **Determinism** (`determinism`) — Whether a model returns the same output for the same input every time — LLMs are non-deterministic by default.
  - Source: authored
- **Greedy Decoding** (`greedy-decoding`) — Always pick the single highest-probability next token — deterministic but can be repetitive.
  - Source: authored
- **Prefill** (`prefill`) — The compute-heavy first phase where the model ingests the whole prompt in parallel.
  - Source: authored
- **Sampling** (`sampling`) — Drawing the next token randomly from the model's probability distribution rather than always taking the top one.
  - Source: authored
- **Temperature** (`temperature`) — A knob for randomness in generation — low is focused/deterministic, high is creative/diverse.
  - Source: authored
- **vLLM** (`vllm`) — A high-throughput LLM serving engine; its PagedAttention manages the KV-cache like virtual memory.
  - Source: qukaizen/docs/TECHNIQUES.md

### Data & Numeric Pathologies

- **fp16 overflow (loss scale overflow)** (`fp16-overflow`) — fp16's limited dynamic range causes activations or gradients to overflow to inf.
  - Source: PyTorch AMP GradScaler docs (pytorch.org/docs/stable/amp.html); NVIDIA mixed-precision guide

### Performance

- **Continuous Batching** (`continuous-batching`) — Swapping requests in and out of a running batch every step to keep the GPU saturated.
  - Source: QuKaiZen AI Dictionary
- **FlashAttention** (`flashattention`) — An exact attention kernel that is fast and memory-light by never materializing the full attention matrix.
  - Source: authored
- **Kernel Fusion** (`kernel-fusion`) — Combine multiple GPU operations into one kernel to cut memory round-trips and launch overhead.
  - Source: authored
- **KV-Cache** (`kv-cache`) — Cached key/value tensors from past tokens so generation does not recompute the whole sequence each step.
  - Source: authored
- **Latency** (`latency`) — The delay before and during a model's response — time-to-first-token and per-token time.
  - Source: QuKaiZen AI Dictionary
- **Layer Streaming** (`layer-streaming`) — Load one transformer layer from disk, compute, discard — running 400B+ models on tiny VRAM.
  - Source: knowledge_base/wiki/concepts/Layer_Streaming_Inference.md
- **Memory Bandwidth** (`memory-bandwidth`) — How fast data moves between memory and compute — the usual bottleneck for LLM inference.
  - Source: authored
- **Prompt Caching** (`prompt-caching`) — Provider-side cache that bills a repeated prompt prefix at a fraction of fresh-input cost on cache hit.
  - Source: authored
- **Speculative Decoding** (`speculative-decoding`) — A small draft model proposes several tokens; the big model verifies them in one pass — lossless speedup.
  - Source: knowledge_base/wiki/concepts/speculative-decoding.md
- **Throughput** (`throughput`) — How many tokens a system generates per unit time, across all requests.
  - Source: QuKaiZen AI Dictionary

### Quantization

- **AWQ** (`awq`) — Low-bit quantization that protects the small fraction of weights tied to large activations, preserving accuracy.
  - Source: authored
- **BF16** (`bf16`) — A 16-bit float with the same exponent range as FP32 — the default precision for training LLMs.
  - Source: authored
- **Calibration** (`calibration`) — Running a small representative dataset through a model to set quantization ranges or scales.
  - Source: authored
- **FP8** (`fp8`) — An 8-bit floating-point format for faster training and inference on H100-class hardware.
  - Source: authored
- **GPTQ** (`gptq`) — A one-shot, layer-by-layer post-training quantization method that minimizes per-layer error using second-order info.
  - Source: authored
- **INT4** (`int4`) — 4-bit integer weights — the aggressive quantization that makes big models fit on small hardware.
  - Source: knowledge_base/wiki/concepts/Quantization_SNR_Affine.md
- **INT8** (`int8`) — 8-bit integer representation — a common, low-risk quantization that roughly halves memory versus 16-bit.
  - Source: authored
- **Mixed Precision** (`mixed-precision`) — Use lower precision for most math but keep sensitive parts in higher precision for stability.
  - Source: authored
- **NF4** (`nf4`) — A 4-bit 'normal float' data type, used in QLoRA, tuned for the bell-curve distribution of weights.
  - Source: authored
- **Quantization** (`quantization`) — Storing weights/activations in fewer bits (FP16 to INT4) to shrink models and speed inference.
  - Source: knowledge_base/wiki/concepts/Quantization_SNR_Affine.md

### QuKaiZen Stack

- **Convergence Graduation** (`convergence-graduation`) — A model graduates when the adversarial swarm gives up trying to break it — not at a fixed cycle limit.
  - Source: QuKaiZen NUCLEUS_AGENT_PROTOCOL
- **Nucleus (bake engine)** (`nucleus-bake-engine`) — [ROADMAP] QuKaiZen's training pipeline for baking domain-specialist SLMs.
  - Source: QuKaiZen CLAUDE.md (Nucleus: company hub, bake pipeline); QuKaiZen THEME.md; QuKaiZen VISION.md
- **Nucleus Seal** (`nucleus-seal`) — An Ed25519 cryptographic provenance chain proving how a Super Skill model was made.
  - Source: QuKaiZen NUCLEUS_AGENT_PROTOCOL
- **Provenance** (`provenance`) — A verifiable record of exactly what went into a model and how it was built.
  - Source: QuKaiZen AI Dictionary
- **SSDP** (`ssdp`) — QuKaiZen's pipeline that distills deep reasoning from frontier teacher models into small, owned Super Skill models.
  - Source: QuKaiZen NUCLEUS_AGENT_PROTOCOL
- **Super Skill** (`super-skill`) — A 1-7B model that durably knows a domain, distilled from a frontier teacher and owned forever.
  - Source: QuKaiZen NUCLEUS_AGENT_PROTOCOL
- **The bake (sealed specialist SLM)** (`the-bake`) — [ROADMAP] The sealed domain-specialist SLM produced by the Nucleus pipeline — the one bet.
  - Source: QuKaiZen CLAUDE.md ('the bake is the moat', 'the one bet'); QuKaiZen THEME.md; QuKaiZen VISION.md

### RL & Alignment

- **Alignment** (`alignment`) — Making a model's behavior match human intent and values.
  - Source: QuKaiZen AI Dictionary
- **Constitutional AI** (`constitutional-ai`) — Align a model to an explicit written set of principles, using the model to critique and revise its own outputs.
  - Source: authored
- **DPO** (`dpo`) — Align to preferences directly from good/bad answer pairs — no reward model or RL loop.
  - Source: authored
- **Guardrails** (`guardrails`) — Runtime checks around a model that block, filter, or reshape unsafe inputs and outputs.
  - Source: authored
- **IPO** (`ipo`) — A DPO variant that adds regularization to avoid overfitting to deterministic preferences.
  - Source: authored
- **KL Divergence** (`kl-divergence`) — A measure of how far one distribution is from another — used to keep an RL-tuned model near its base.
  - Source: authored
- **ORPO** (`orpo`) — A single-stage method that combines instruction tuning and preference alignment without a separate reward model or reference model.
  - Source: authored
- **PPO** (`ppo`) — The RL algorithm classically used to optimize a model against a reward model in RLHF.
  - Source: authored
- **Preference Data** (`preference-data`) — Datasets of 'A is better than B' human judgments used to train reward models or do DPO.
  - Source: authored
- **Red-Teaming** (`red-teaming`) — Deliberately probing a model with adversarial inputs to surface harmful, unsafe, or broken behavior.
  - Source: authored
- **Reward Model** (`reward-model`) — A model trained to score outputs by human preference, providing the reward signal for RLHF.
  - Source: authored
- **RLAIF** (`rlaif`) — Like RLHF, but the preference labels come from an AI judge instead of (or alongside) humans.
  - Source: authored
- **RLHF** (`rlhf`) — Align a model to human preferences via a reward model trained on human rankings, then RL.
  - Source: authored

### Training Symptoms

- **Diverging loss** (`diverging-loss`) — Training loss climbs without bound instead of decreasing.
  - Source: PyTorch amp docs; OLMo training logbook (EleutherAI/OLMo, 2024)
- **Loss plateau** (`loss-plateau`) — Loss stops improving for many steps — training is stalled.
  - Source: Goodfellow, Bengio & Courville — Deep Learning ch.8; HF Trainer docs (lr_scheduler_type)
- **NaN loss** (`nan-loss`) — Loss value becomes Not-a-Number — the run is numerically broken.
  - Source: PyTorch AMP / GradScaler docs (pytorch.org/docs/stable/amp.html); NVIDIA mixed-precision guide
- **Slow convergence** (`slow-convergence`) — Loss decreases, but far more slowly than expected for the compute budget.
  - Source: Goodfellow et al. — Deep Learning ch.8; HF Trainer docs; OLMo training logbook
- **Vanishing gradients** (`vanishing-gradients`) — Gradients shrink toward zero in early layers — no useful learning signal.
  - Source: Goodfellow et al. — Deep Learning §10.7; Karpathy nanoGPT architectural notes

### Training

- **AdamW** (`adamw`) — The default optimizer for training transformers — Adam with decoupled weight decay.
  - Source: authored
- **Backprop** (`backprop`) — The algorithm that computes how to nudge every weight by propagating error gradients backward.
  - Source: authored
- **Batch Size** (`batch-size`) — How many training examples are processed before each weight update.
  - Source: authored
- **Catastrophic Forgetting** (`catastrophic-forgetting`) — When fine-tuning on a new task erases capabilities the model previously had.
  - Source: authored
- **Checkpoint** (`checkpoint`) — A saved snapshot of model weights (and often optimizer state) you can resume or deploy from.
  - Source: authored
- **Data Parallelism** (`data-parallelism`) — Replicate the model across devices, split the batch, and average gradients each step.
  - Source: authored
- **Dropout** (`dropout`) — Randomly zeroing activations during training to prevent overfitting.
  - Source: authored
- **Eval** (`eval`) — The practice of measuring model quality with repeatable tests — from public benchmarks to task-specific graders.
  - Source: authored
- **FSDP** (`fsdp`) — Shards model parameters, gradients, and optimizer state across GPUs so huge models fit in training.
  - Source: authored
- **Gradient** (`gradient`) — The vector of partial derivatives telling how the loss changes as you tweak each weight.
  - Source: authored
- **Gradient Accumulation** (`gradient-accumulation`) — Sum gradients over several micro-batches before updating, simulating a large batch on limited memory.
  - Source: authored
- **Gradient Clipping** (`gradient-clipping`) — Cap the gradient's magnitude each step to prevent exploding updates from destabilizing training.
  - Source: authored
- **GSM8K** (`gsm8k`) — Around 8,500 grade-school math word problems that test multi-step arithmetic reasoning.
  - Source: authored
- **Learning Rate** (`learning-rate`) — How big a step the optimizer takes down the gradient — the most consequential training hyperparameter.
  - Source: authored
- **Loss Function** (`loss-function`) — The scalar that measures how wrong a model's predictions are — what training minimizes.
  - Source: authored
- **Mixed-precision training** (`mixed-precision-training`) — Use fp16 or bf16 for forward/backward passes while keeping fp32 master weights.
  - Source: PyTorch AMP docs (torch.cuda.amp); NVIDIA mixed-precision training guide; HF Trainer docs (fp16, bf16)
- **MMLU** (`mmlu`) — A benchmark of ~16,000 multiple-choice questions across 57 subjects, measuring an LLM's breadth of knowledge.
  - Source: authored
- **Overfitting** (`overfitting`) — When a model memorizes training-set quirks and fails to generalize to new data.
  - Source: authored
- **Pretraining** (`pretraining`) — The first, largest training stage: learn general language/knowledge from a huge unlabeled corpus.
  - Source: authored
- **Regularization** (`regularization`) — Any technique that constrains a model to generalize better rather than memorize the training set.
  - Source: authored
- **Scaling Laws** (`scaling-laws`) — Empirical power-law curves showing model loss falls predictably as parameters, data, and compute grow.
  - Source: authored
- **SFT** (`sft`) — Plain supervised training on curated input to output examples — the first step of post-training.
  - Source: authored
- **Warmup** (`warmup`) — Ramping the learning rate up from near zero over the first steps to avoid early instability.
  - Source: authored
- **Weight Decay** (`weight-decay`) — A penalty that nudges weights toward zero each step, discouraging overly large parameters and overfitting.
  - Source: authored

<!-- dac:world_sha256 c1914ba993a75f2f9cfa0dc3b08f91f50a2d27f1e5ae7b1487930f2665f29d4b -->
