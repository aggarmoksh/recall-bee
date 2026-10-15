"""Voiceover script as phrases (shared by make_edit.py and align_vo.py). \"||\" forces a caption break and is never spoken."""

PHRASES = {
    3: ["Bee hears everything I learn:", "lectures, study sessions, podcasts.", "It can summarize them and remember them for me.",
        "But in an interview,", "I'm the one who has to know it."],
    4: ["Practicing recall is one of the best-tested ways to learn.", "Recall turns what Bee heard into quizzes on my wrist",
        "that I answer out loud.", "Bee's transcript is the answer sheet."],
    5: ["When a learning session ends,", "Recall pulls the transcript through the Bee CLI", "and skips anything casual or personal.",
        "Claude Haiku on Amazon Bedrock writes the questions,", "each tied to the moment I heard it.",
        "When I'm free, I say so, or tick a todo.", "Each question goes out as a Bee todo with an alarm.",
        "Recall reads Bee's live transcript while I'm talking,", "grades me against a rubric,", "and sends feedback the same way.",
        "Spaced repetition decides when I see it again."],
    6: ["My first version quizzed me at random times.", "Six prompts, four missed:", "driving, asleep, or the alert hit my phone.",
        "So Recall waits for you.", "It nudges after a lecture and in your active hours,", "holds off while Bee hears you talking,",
        "and a missed question never counts against you."],
    7: ["In my first two real sessions,", "I started each one with a single sentence", "and answered all twelve questions."],
    8: ["Recall only quizzes on learning sessions.", "Casual and personal conversations are skipped,", "and it never touches audio.",
        "This page shows what it ignored, and why."],
    10: ["Built on the Bee CLI and Amazon Bedrock,", "open source under MIT.", "||", "Bee remembers what you heard.", "Recall makes sure you do."],
}
