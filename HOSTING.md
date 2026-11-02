# Hosting: how Anchor runs today, and its limits

> **AI Lodge showcase:** the hosted demo now runs on Vercel + Cloud Run + Neon,
> reshaped to fit (README, "This demo"). This file describes the student's own
> Mac-hosted setup, whose scripts are in git history.

As of 18 October 2026, Anchor is split in two. The public pages run on Vercel;
the app itself runs on the owner's Mac. This file explains why, and what that
costs in availability and speed.

## The current setup

```
Visitor
  │
  ├─► https://anchor-study.vercel.app            (Vercel, always on)
  │     Front page, Uploads, Connectors, Pricing, FAQ, demo clips,
  │     and saved sample answers in the try-it box. Static files only:
  │     no sign-in, no AI calls, works even when the app is off.
  │
  └─► "Sign in" / "Try Anchor" buttons
        │
        ▼
      https://els-macbook-air.tail0939c1.ts.net   (the owner's MacBook Air,
        │                                           shared by Tailscale Funnel)
        ├─ The app: FastAPI, chat, uploads, Sources panel
        ├─ Sign-in: WorkOS AuthKit (hosted login page)
        ├─ Postgres with pgvector: every Library's searchable passages
        ├─ DuckDB: uploaded spreadsheets
        ├─ Uploaded files and their parsed text, on the Mac's disk
        ├─ Docling: reads PDFs, Word and PowerPoint files
        ├─ bge-reranker-v2-m3: ranks search results, on the Mac's CPU
        └─ Calls to OpenAI: answering, checking, embeddings, reading pictures
```

`scripts/serve_public.sh` starts the app and shares it. It refuses to start
unless the login service is set up and the development sign-in is off.
`scripts/deploy_site.sh` rebuilds and redeploys the Vercel pages.

## Why the app is not fully online

The app was measured on 18 October, and no free host could run it as it is:

| What the app needs | Measured |
| --- | --- |
| Memory | about 3.9 GB with the reranker loaded, more while reading a PDF |
| A disk that keeps files | uploads, spreadsheets, parsed text, picture crops |
| Long background work | reading a 20-page PDF with equations took 636 s; a 10-page PDF with 2 pictures took 155 s |
| CPU | the reranker reads about 23,000 tokens per question; 21-34 s on the Mac |

| Host | Why not (checked 18 Oct 2026) |
| --- | --- |
| Vercel (whole app) | 2 GB memory on the free plan (4 GB paid), 300 s limit per request on the free plan, 4.5 MB per upload request, no lasting disk, shuts down after 5 idle minutes |
| Railway free | 0.5 GB memory, 0.5 GB disk; the reranker model alone is 2.2 GB |
| Render free | 512 MB memory |
| Hugging Face Spaces | 16 GB free hardware, but apps like this need the paid PRO plan |
| Oracle Always Free | 12 GB fits, but 2 CPUs: estimated 3-7 minutes per answer |
| Google Cloud Run | free tier possible, but needs a rework: no disk, no background work |
| Railway Hobby (paid) | would run it; estimated $40-45 a month, mostly for memory |

## Limitations of the current setup

**Availability**
- The app is up only while the Mac is on, awake, online, and running
  `serve_public.sh`. Closing the MacBook Air's lid puts it to sleep, and the
  app goes down with it.
- There is no second server: a restart, a power cut or a home network
  outage takes the app offline.
- When the app is down, the Vercel pages still load, but "Sign in" and
  "Try Anchor" fail.

**Speed and capacity**
- An answer takes about 30-40 s, mostly the reranker on CPU (keyword search
  0.02 s, vector search 0.34 s). The answer streams in as it is written.
- Questions are answered one at a time. A second person asking at the same
  moment waits about 25 s more.
- Uploads are read one at a time, so a long PDF delays everyone else's.
- Tailscale Funnel has bandwidth limits that cannot be changed.

**Running costs and limits**
- Model calls draw on a $5 OpenAI demo budget (raised from $1 on 21 Oct). Members get 1 file (up to 50
  pages) and 3 questions; the app stops answering when the budget runs out.
- Sign-in uses WorkOS staging keys, which are fine for a demo but not for a
  real launch.
- Nothing is backed up: the database and uploaded files live only on the
  Mac's disk.

**Privacy**
- Uploaded files stay on the owner's Mac, not on a cloud host. Their text and
  pictures are still sent to OpenAI to be read and answered from.

## What full hosting would take

- A server with about 8 GB of memory, a disk that keeps files, and at least
  4 fast CPUs. Railway Hobby is the simplest paid option (estimated $40-45 a
  month).
- Or a lighter search step (a smaller or compressed reranker, or fewer
  passages reranked) so a free 2-CPU server like Oracle's becomes usable.
  Any such change alters which passages reach the model, so the evaluation
  must be re-run before it is kept.
- Production WorkOS keys, regular backups, and a fixed domain for both halves.
