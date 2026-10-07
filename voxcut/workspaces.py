"""'I work as...' presets: one app, tuned for different professionals.

Each workspace only sets *defaults* (which tab opens, file names, subtitle look, prompt templates for Create video).
Nothing is locked - every tool stays available to everyone.
"""

WORKSPACES = {
    "General": {
        "tagline": "Record, edit, caption and create videos.",
        "tab": "Lecture recorder", "file_name": "Recording",
        "subtitle": {"position": "Bottom", "size": "Medium", "highlight": False},
        "templates": [
            ("Short promo", "A short promo video about [your topic]: what it is, who it helps, and a clear call to action. Friendly, upbeat tone."),
            ("Tips reel", "A five-part tips reel about [your topic] with one practical tip per part and a closing thank-you."),
        ]},
    "Tutor / Lecturer": {
        "tagline": "Record lessons of any length, teach on a whiteboard, caption and share.",
        "tab": "Lecture recorder", "file_name": "Lecture",
        "subtitle": {"position": "Bottom", "size": "Medium", "highlight": False},
        "templates": [
            ("Revision summary", "A five-part revision summary of [topic] for students: the key ideas, a worked example, common mistakes, and a closing tip. Clear, encouraging tone."),
            ("Course promo", "A short promo for my [subject] classes: who they are for, what students will learn, results so far, and how to enrol. Warm and confident."),
            ("Exam-week encouragement", "A calm, encouraging message to students before exams: study tips, rest, and believing in yourself."),
        ]},
    "Church / Ministry": {
        "tagline": "Sermons, verses and testimonies - captioned and ready to share.",
        "tab": "Subtitles", "file_name": "Sermon",
        "subtitle": {"position": "Bottom", "size": "Large", "highlight": True},
        "templates": [
            ("Verse of the day", "A short, peaceful video sharing a verse about [theme] with a brief reflection and a closing blessing. Warm, faith-filled tone."),
            ("Sunday invitation", "A warm invitation to our Sunday service: time, place, what to expect, and a welcome to visitors."),
            ("Testimony highlight", "A five-part story of gratitude and answered prayer about [topic], ending with an encouraging message."),
        ]},
    "Radio / Podcast": {
        "tagline": "Turn audio into captioned video clips people can watch on mute.",
        "tab": "Subtitles", "file_name": "Episode",
        "subtitle": {"position": "Middle", "size": "Large", "highlight": True},
        "templates": [
            ("Episode teaser", "A 30-second teaser for my episode about [topic]: a hook question, two key points, and where to listen."),
            ("Quote card video", "A five-part video of the best quotes from my episode about [topic], each with a short setup line."),
        ]},
    "Business / Marketing": {
        "tagline": "Promos, product videos and customer stories without an agency.",
        "tab": "Create video (AI)", "file_name": "Promo",
        "subtitle": {"position": "Bottom", "size": "Medium", "highlight": True},
        "templates": [
            ("Product promo", "A five-part promo for [product]: the problem it solves, how it works, key benefits, proof or testimonials, and a call to action."),
            ("Customer story", "A short customer success story: who they are, their challenge, how we helped, and the result."),
            ("Event announcement", "An announcement video for [event]: date, place, who should come, what they will gain, and how to register."),
        ]},
    "Training / HR / NGO": {
        "tagline": "Onboarding, training and programme updates your team can actually follow.",
        "tab": "Lecture recorder", "file_name": "Training",
        "subtitle": {"position": "Bottom", "size": "Medium", "highlight": False},
        "templates": [
            ("Onboarding welcome", "A friendly welcome for new team members at [organisation]: who we are, our values, first-week steps, and who to ask for help."),
            ("Safety / policy reminder", "A clear, calm reminder about [policy or safety topic]: why it matters, the three key rules, and what to do if something goes wrong."),
            ("Programme impact update", "A short impact update on [programme]: the need, what we did, the results so far, and how supporters can help."),
        ]},
    "Software demos / Tutorials": {
        "tagline": "Screen-record walkthroughs with captions, highlights and chapters.",
        "tab": "Lecture recorder", "file_name": "Demo",
        "subtitle": {"position": "Bottom", "size": "Medium", "highlight": False},
        "templates": [
            ("Feature walkthrough intro", "An intro for a walkthrough of [feature]: what problem it solves, what viewers will learn, and a quick overview."),
            ("Release notes video", "A short 'what's new' video for version [x]: the top three improvements and how to try them."),
        ]},
    "Interviews / Journalism": {
        "tagline": "Transcribe, caption and clip interviews quickly.",
        "tab": "Subtitles", "file_name": "Interview",
        "subtitle": {"position": "Bottom", "size": "Medium", "highlight": False},
        "templates": [
            ("Interview highlight", "A short highlight video from my interview with [guest] about [topic]: the strongest quote, one insight, and where to read or watch more."),
        ]},
    "Coach / Consultant": {
        "tagline": "Record sessions and workshops, send clean captioned recaps.",
        "tab": "Lecture recorder", "file_name": "Session",
        "subtitle": {"position": "Bottom", "size": "Medium", "highlight": False},
        "templates": [
            ("Workshop recap", "A five-part recap of my [topic] workshop: the main idea, three takeaways, one exercise to try, and next steps."),
            ("Service intro", "A short introduction to my [service]: who it is for, the outcome clients get, how it works, and how to book."),
        ]},
}


def names():
    return list(WORKSPACES)
