# Recall

Bee captures sustained learning speech (lectures, study sessions, family conversations in any language). An agent turns those transcripts into spaced-retrieval questions and delivers them as Bee todo alarms. The wearer answers by explaining the answer out loud for 45-60 seconds; Bee's ambient capture transcribes it, an LLM grades the explanation against the source transcript, and SM-2 reschedules the next prompt. A target-language flag lets the same loop serve students and bilingual families.

Status: in development.

## Setup

(placeholder -- filled in as the backend skeleton lands)

```
py -3.12 -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY etc.
```
