from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApiException,
)
import google.generativeai as genai
import os
import re
import json

app = FastAPI()

# 프론트엔드(Vercel)와의 통신을 위한 CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gemini AI 설정
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("WARNING: GEMINI_API_KEY is not set in environment variables.")

# 가장 안정적인 gemini-1.5-flash 모델 사용
model = genai.GenerativeModel("gemini-1.5-flash")


def extract_video_id(url: str):
    """유튜브 URL에서 비디오 ID를 추출하는 함수"""
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
    return match.group(1) if match else None


def extract_json_text(raw_text: str) -> str:
    """Gemini 응답 텍스트에서 JSON 부분만 안전하게 추출"""
    if not raw_text:
        raise ValueError("Gemini returned an empty response")

    cleaned = raw_text.strip()

    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    # 코드블록이 아니어도 JSON 앞뒤에 문장이 붙는 경우를 대비
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        cleaned = cleaned[start_idx : end_idx + 1]

    return cleaned


def fetch_best_transcript(video_id: str):
    """가장 적절한 자막을 가져온다. 영어 우선, 필요시 번역 자막 사용"""
    if not hasattr(YouTubeTranscriptApi, "list_transcripts"):
        # 구버전 fallback
        print("WARNING: Using legacy get_transcript method due to old library version.")
        data = YouTubeTranscriptApi.get_transcript(video_id, languages=["en"])
        return data

    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

    try:
        transcript = transcript_list.find_manually_created_transcript(["en"])
        print("Found manual English transcript.")
        return transcript.fetch()
    except NoTranscriptFound:
        pass

    try:
        transcript = transcript_list.find_generated_transcript(["en"])
        print("Found auto-generated English transcript.")
        return transcript.fetch()
    except NoTranscriptFound:
        pass

    try:
        transcript = transcript_list.find_transcript(["en"])
        print("Found available English transcript.")
        return transcript.fetch()
    except NoTranscriptFound:
        pass

    # 영어 자막이 없으면, 번역 가능한 자막이 있으면 영어로 번역 시도
    for transcript in transcript_list:
        if transcript.is_translatable:
            try:
                translated = transcript.translate("en")
                print(f"Translated {transcript.language_code} transcript to English.")
                return translated.fetch()
            except Exception as translate_error:
                print(f"Translation failed for {transcript.language_code}: {translate_error}")

    raise NoTranscriptFound(video_id, ["en"], transcript_list)


@app.get("/")
def health_check():
    """서버 상태 확인용 엔드포인트"""
    return {"status": "ok", "message": "Server is running"}


@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    print(f"--- Starting analysis for: {video_url} ---")
    video_id = extract_video_id(video_url)

    if not video_id:
        print("Error: Invalid YouTube URL")
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Server configuration error: GEMINI_API_KEY is missing")

    try:
        # 1. 유튜브 자막 추출 단계
        print(f"Attempting to fetch transcript for video: {video_id}")
        data = fetch_best_transcript(video_id)

        if not data:
            raise HTTPException(status_code=422, detail="No usable transcript segments found")

        full_text = " ".join([t.get("text", "") for t in data]).strip()
        if not full_text:
            raise HTTPException(status_code=422, detail="Transcript text is empty")

        print(f"Successfully fetched transcript. Length: {len(full_text)} characters.")

        # 2. AI 분석 단계 (Gemini)
        print("Sending prompt to Gemini AI...")
        prompt = f"""
        당신은 전문 번역가이자 언어 학습 가이드입니다.
        제공된 유튜브 영어 스크립트를 문맥에 따라 3~5문장씩 의미 단위로 나누어 번역해주세요.
        또한 학습하기 좋은 주요 단어 5개를 선정해주세요.

        반드시 아래 JSON 형식으로만 응답하세요:
        {{
            "script": [
                {{"text": "English sentences...", "translation": "한국어 번역..."}}
            ],
            "vocab": [
                {{"word": "단어", "meaning": "뜻"}}
            ]
        }}

        스크립트: {full_text[:7000]}
        """

        response = model.generate_content(prompt)
        response_text = extract_json_text(getattr(response, "text", ""))
        print("Received response from Gemini AI.")

        ai_result = json.loads(response_text)

        if not isinstance(ai_result, dict) or "script" not in ai_result or not isinstance(ai_result["script"], list):
            raise ValueError("Gemini response JSON does not include valid 'script' list")
        if "vocab" not in ai_result or not isinstance(ai_result["vocab"], list):
            ai_result["vocab"] = []
        if len(ai_result["script"]) == 0:
            raise ValueError("Gemini response 'script' is empty")

        # 3. 타임라인(시작 시간) 매칭
        chunk_size = max(1, len(data) // len(ai_result["script"]))
        for i, item in enumerate(ai_result["script"]):
            idx = min(i * chunk_size, len(data) - 1)
            item["start"] = data[idx].get("start", 0)
            item["id"] = i + 1
            item["speaker"] = "Speaker"

        print("Analysis complete. Sending data to frontend.")
        return ai_result

    except HTTPException:
        raise
    except (NoTranscriptFound, TranscriptsDisabled):
        raise HTTPException(status_code=422, detail="분석 실패: 영어 자막(또는 영어로 역 가능한 자막)이 없는 영상입니다.")
    except VideoUnavailable:
        raise HTTPException(status_code=404, detail="분석 실패: 영상을 찾을 수 없거나 비공개/삭제 상태입니다.")
    except YouTubeTranscriptApiException as yta_error:
        print(f"YouTube transcript API error: {yta_error}")
        raise HTTPException(status_code=502, detail="분석 실패: 자막 서버와 통신 중 오류가 발생했습니다.")
    except json.JSONDecodeError as json_error:
        print(f"Gemini JSON parse error: {json_error}")
        raise HTTPException(status_code=502, detail="분석 실패: AI 응답 형식(JSON)을 파싱하지 못했습니다.")
    except Exception as e:
        error_msg = str(e)
        print(f"CRITICAL ERROR: {error_msg}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {error_msg}")
