# Workflow Timing Report

End-to-end timing for the computational-taxonomy-development pipeline, from raw data-point description generation through expert evaluation. Models within a stage and annotators within the evaluation stage all run in parallel; stages themselves run sequentially.

## Stage 1 — Description generation (parallel across models)

| Model | Window | Elapsed |
|---|---|---|
| Apertus-8B-Instruct | 7:16 AM – 11:12 AM | 3 h 56 min (236 min) |
| GPT5-nano | 9:52 AM – 10:41 AM | 49 min |
| Phi-4-mini-instruct | 7:59 AM – 10:08 AM | 2 h 09 min (129 min) |

**Stage wall-clock (parallel): 3 h 56 min** — bounded by Apertus.

### Compute resources per model run

Each description-generation notebook was submitted as an independent SLURM job:

```
sbatch --gpus=2 --gres=gpumem:40g --time=05:00:00 --mem-per-cpu=32g \
  --wrap="jupyter nbconvert --to notebook --execute <script>.ipynb --inplace"
```

| Resource | Allocation per job |
|---|---|
| GPUs | 2 |
| GPU memory | 40 GB per GPU (80 GB total) |
| CPU memory | 32 GB per CPU core |
| Wall-clock limit | 5 h (`--time=05:00:00`) |

All three model jobs ran within the 5 h cap (Apertus consumed 79 % of budget, Phi-4-mini 43 %, GPT5-nano 16 %). Aggregate stage allocation across the three concurrent jobs: **6 GPUs, 240 GB GPU memory, 96 GB CPU memory** for up to 5 h. GPT5-nano is an API-hosted model, so although the same SLURM template was used, its GPU allocation was effectively idle during the request loop.

Verification via `sacct -j <jobid> --format=JobID,AllocTRES%100` on representative jobs showed the scheduler matched the requests to **2× NVIDIA RTX PRO 6000** (96 GB GDDR7 per card, ~192 GB aggregate per job) — well above the 40 GB-per-GPU minimum. The `--gres=gpumem:40g` flag was therefore used only as a scheduling filter, not as a binding constraint; the actual VRAM available per job was ~4.8× what was requested.

### Why Stage 1 dominates the pipeline wall-clock

Stage 1 wall-clock is not bounded by GPU memory — Apertus-8B fits in ~16 GB of bf16 weights, vastly less than the ~192 GB available per job — but by **memory bandwidth × number of sequential token decodes**. Each output token requires streaming the full weight tensor through the GPU's GDDR, and tokens must be generated sequentially (token *N+1* depends on token *N*). For roughly 1909 data-point queries × ~100–200 output tokens each, total output is on the order of 2–4 × 10⁵ tokens; at ~30–50 ms per token (typical for an 8B model on this hardware), this alone accounts for 100–320 minutes — already in the observed range. Long RAG-style input contexts (multiple regulatory passages from CPR/ESPR per query) inflate prefill cost on top.

Three corollaries follow:

1. **Allocating more GPU memory would not have shortened Stage 1.** The model already fits; headroom is wasted.
2. **The second GPU is largely idle** for sequential, single-stream inference of an 8B model — tensor parallelism over two cards yields sub-linear speed-up due to communication overhead, and LlamaIndex pipelines do not enable it by default.
3. **The single highest-leverage optimisation is request batching.** Processing 8–16 RAG queries together would amortise the weight-streaming cost across many concurrent decodes, plausibly yielding a 5–10× throughput improvement on the same hardware. Phi-4-mini's faster wall-clock (2 h 09 min vs 3 h 56 min) is explained by its smaller parameter count (~3.8 B vs 8 B → roughly half the per-token cost). GPT5-nano's 49 min comes from running on OpenAI-hosted infrastructure (H100/B200-class hardware with continuous batching), a different cost model entirely.

## Stage 2 — Clustering optimisation (parallel across sources)

| Source | Window | Elapsed |
|---|---|---|
| Data-point names | 7:05 PM – 8:25 PM | 1 h 20 min (80 min) |
| Apertus descriptions | 7:32 PM – 8:26 PM | 54 min |
| GPT5-nano descriptions | 7:35 PM – 8:27 PM | 52 min |
| Phi-4-mini descriptions | 11:49 AM – 12:45 PM | 56 min |

**Stage wall-clock (parallel): 1 h 20 min** — bounded by the data-point-names run.

### Compute resources per clustering run

Each clustering-optimisation notebook was submitted as an independent SLURM job:

```
sbatch --gpus=1 --gres=gpumem:10g --time=05:00:00 --mem-per-cpu=32g \
  --wrap="jupyter nbconvert --to notebook --execute 02_251218_optimizing_number_of_clusters_<dataset name>.ipynb --inplace"
```

| Resource | Allocation per job |
|---|---|
| GPUs | 1 |
| GPU memory | 10 GB |
| CPU memory | 32 GB per CPU core |
| Wall-clock limit | 5 h (`--time=05:00:00`) |

All four jobs ran well within the 5 h cap (data-point names 27 % of budget, Apertus 18 %, Phi-4-mini 19 %, GPT5-nano 17 %). Aggregate stage allocation across the four concurrent jobs: **4 GPUs, 40 GB GPU memory, 128 GB CPU memory** for up to 5 h. Compared to Stage 1, each clustering job uses half the GPU count and a quarter of the per-GPU memory, reflecting the lighter footprint of UMAP + HDBSCAN sweeps relative to LLM inference.

## Stage 3 — Annotator evaluation (parallel across annotators)

Source: `evaluation_timer.json` under `taxonomy_curator/annotator_results/annotator <n>/`.

| Annotator | `elapsed_seconds` | Elapsed |
|---|---|---|
| Annotator 1 | 6 788.12 | 1 h 53 min 08 s |
| Annotator 2 | 6 851.38 | 1 h 54 min 11 s |
| Annotator 3 | 16 608.57 | 4 h 36 min 49 s |

**Stage wall-clock (parallel): 4 h 36 min 49 s** — bounded by Annotator 3.

## End-to-end totals

- **Parallel wall-clock** (sum of stage maxes, since the three stages run sequentially):
  3 h 56 min + 1 h 20 min + 4 h 37 min = **9 h 53 min**

- **Sequential cost** (sum of every item, i.e. compute + person-hours if nothing parallelised):

  | Stage | Sequential sum |
  |---|---|
  | Description generation | 236 + 49 + 129 = 414 min |
  | Clustering optimisation | 80 + 54 + 52 + 56 = 242 min |
  | Annotator evaluation | 113 + 114 + 277 = 504 min |
  | **Total** | **1 160 min ≈ 19 h 20 min** |

- **Parallelisation factor:** 1 160 / 593 ≈ **1.96×**.

The pipeline completes in roughly one working day of wall-clock, against just under two days of cumulative compute + annotator time.
