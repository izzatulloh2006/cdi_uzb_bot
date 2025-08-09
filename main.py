import os
import uuid
import asyncio
import logging
import re
import fitz  # PyMuPDF
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, FSInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import google.generativeai as genai
from pdf2image import convert_from_path
import pytesseract
from PIL import Image
import html

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(__file__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "7493711710:AAGhI45mwzvjDt8JlQoK3CA0XUm7sBQwXAg")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Bot va Dispatcher sozlash
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

def preprocess_ocr_text(text):
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[-‐‑–—]+", "-", text)
    text = re.sub(r"[^\w\s.,?!()]", "", text)
    return text.strip()

# PDFdan matn olish (avval PyMuPDF, so‘ng OCR fallback)
def pdf_to_text(file_path):
    try:
        doc = fitz.open(file_path)
        full_text = ""
        for page in doc:
            text = page.get_text().strip()
            full_text += text + "\n"
        doc.close()

        if not full_text.strip():
            images = convert_from_path(file_path)
            for img in images:
                ocr_text = pytesseract.image_to_string(img, lang="eng+uz")
                full_text += preprocess_ocr_text(ocr_text) + "\n"

        return full_text.strip()

    except Exception as e:
        logger.error(f"pdf_to_text xatosi: {e}")
        return None


def split_passage_questions_answers(text):
    if not text:
        return "", "", []

    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    question_indicators = [
        r"questions\s+\d+-\d+",
        r"task\s+\d+",
        r"you should spend about \d+ minutes on questions",
        r"^\d+\.\s+",
        r"^\(\d+\)\s+",
        r"^(true|false|not given)",
        r"^[a-d]\.\s+"
    ]

    split_index = None
    for i, line in enumerate(lines):
        if any(re.search(p, line.lower()) for p in question_indicators):
            split_index = i
            break

    if split_index is None:
        # fallback strategy: middle of the text
        split_index = int(len(lines) * 0.5)
        for i in range(max(0, split_index - 5), min(len(lines), split_index + 5)):
            if not lines[i] or "passage" in lines[i].lower() or "questions" in lines[i].lower():
                split_index = i
                break

    passage = "\n".join(lines[:split_index])
    questions_block = "\n".join(lines[split_index:])

    # Split by numbered questions like 1. 2. 3.
    questions = [q.strip() for q in re.split(r"(?=\d+\.\s)", questions_block) if q.strip()]

    # Extract answers (simple strategy)
    answers = re.findall(r"\b(TRUE|FALSE|NOT GIVEN|[A-D])\b", questions_block, re.IGNORECASE)

    return passage, questions, [a.strip().upper() for a in answers if a.strip()]


def generate_html_prompt(text):
    passage, questions, answers = split_passage_questions_answers(text)

    prompt = f"""
Create a complete IELTS Reading Practice HTML page based on the provided content. The page must be a single, standalone HTML file with all CSS and JavaScript inline.

1.  **Structure and Layout:**
    -   Use a two-column layout. The left column is for the reading passage, and the right column is for questions and answer fields.
    -   Include a fixed header at the top with the title "IELTS Reading Practice", a 20-minute countdown timer, and two buttons: "Start Test" and "Check Answers".
    -   The header must be compact. The timer and buttons must be visually grouped together and centered horizontally within the header.
    -   The layout must be responsive.

2.  **Header Section:**
    -   Title: "IELTS Reading Practice".
    -   A 20-minute countdown timer.
    -   Two buttons: "Start Test" and "Check Answers".
    -   The timer and buttons must be visually grouped together and centered horizontally within the header.

3.  **Passage Content (Left Column):**
    -   The passage text is: {html.escape(passage)}
    -   Split the passage into paragraphs, each wrapped in a `<p>` tag, to maintain clear visual separation as in the original text.
    -   The passage should have the title "READING PASSAGE 1" and the instruction "You should spend about 20 minutes on Questions 1-13, which are based on Reading Passage 1 below."

4.  **Questions and Answers (Right Column):**
    -   The questions block is: {html.escape('\n'.join(questions))}
    -   Render each question with its appropriate input type (radio buttons for TRUE/FALSE/NOT GIVEN or multiple choice, text input for ONE WORD answers).
    -   The correct answers for checking are stored in a JavaScript object named `correctAnswers`. The object must be formatted like this: `{str(answers).replace("'", '"')}`. Ensure this object is properly populated with the answers extracted from the source document.
    
5.  **Functionality (JavaScript):**
    -   **"Start Test" button:** When clicked, it should start a 20-minute countdown timer. The "Check Answers" button should be enabled.
    -   **"Check Answers" button:**
        -   Disable the button after it's clicked.
        -   Stop the timer.
        -   Loop through each question to compare the user's answer with the `correctAnswers` object.
        -   Display feedback directly below each question.
        -   Feedback messages must be in Uzbek:
            -   **Correct answer:** `✓ To'g'ri`
            -   **Incorrect answer:** `★ Xato! [question_number]-savol. To'g'ri javob: [correctAnswer]`
            -   **Unanswered (radio/checkbox):** `★ Javob tanlanmagan. To'g'ri javob: [correctAnswer]`
            -   **Empty (text input):** `★ Javob kiritilmagan. To'g'ri javob: [correctAnswer]`
        -   Count the total correct answers.
        -   Display the final score and a calculated Band Score at the bottom of the right column.

6.  **Styling (CSS):**
    -   Use a modern, clean design similar to official IELTS tests, but with a more compact layout.
    -   Font family: Arial.
    -   Background color: `#f4f4f4`.
    -   Use green (`#5cb85c`) for correct feedback and red (`#d9534f`) for incorrect feedback.
    -   All elements, including the header, columns, and question blocks, must use smaller font sizes and reduced padding/margins to achieve a more compact look, similar to the provided first image.
    -   The HTML, CSS, and JavaScript must be entirely contained within the single HTML file, with no external links or files.
    
The final output must be a single, complete, and valid HTML document that can be saved as an `.html` file and function offline.
"""
    return prompt


@dp.message(F.document.mime_type == "application/pdf")
async def handle_pdf(message: types.Message):
    document = message.document
    file_info = await bot.get_file(document.file_id)
    file_path_telegram = file_info.file_path

    temp_pdf_path = os.path.join(BASE_DIR, f"temp_{uuid.uuid4().hex}.pdf")
    html_path = None

    await bot.download_file(file_path_telegram, destination=temp_pdf_path)

    try:
        text = pdf_to_text(temp_pdf_path)
        if not text:
            await message.answer("❗ PDFdan matn topilmadi.")
            return

        prompt = generate_html_prompt(text)
        response = await asyncio.to_thread(model.generate_content, prompt)
        html_content = response.text.strip()

        if "<html" not in html_content.lower():
            await message.answer("❌ HTML tarkibda xatolik bor. Gemini noto‘g‘ri javob berdi.")
            return

        html_path = os.path.join(BASE_DIR, f"{uuid.uuid4().hex}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        await message.answer_document(FSInputFile(html_path), caption="✅ HTML fayl tayyor! Agar sizga bu html sahifa yo'qmasa pdf fayilingizni qayta yuboring, chunki html sahifa AI tomonidan qilinadi")

    except Exception as e:
        logger.exception("Xatolik yuz berdi:")
        await message.answer("❌ Xatolik yuz berdi: " + str(e))

    finally:
        for path in [temp_pdf_path, html_path]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    logger.warning(f"Faylni o‘chirishda xato: {path} - {e}")

@dp.message()
async def default_handler(message: types.Message):
    await message.answer("📄 Iltimos, IELTS reading PDF faylini yuboring.")

# Botni ishga tushirish
if __name__ == "__main__":
    try:
        asyncio.run(dp.start_polling(bot))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to‘xtatildi.")
