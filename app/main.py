import os
import csv
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential

load_dotenv()

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
GPT_MODEL = os.getenv("GPT_MODEL", "gpt-4")

template_prompt = """
指示：与えられた質問に対する回答を評価してください。

質問：{input_text}

模範回答：{output_text}

評価観点：{eval_aspect}

回答者の回答：{pred}

評価基準：
1: 不適切または無関係
2: 部分的に正しいが、重要な要素が欠けている
3: 基本的に正しいが、改善の余地がある
4: ほぼ正確で十分な回答
5: 完璧で模範的な回答

以下の形式でJSON形式で回答してください：
{{
    "reason": "<評価理由>",
    "grade": <int, 1〜5の5段階評価>
}}
"""

# Load tasks from CSV
with open(os.path.join(os.path.dirname(__file__), "..", "data", "elyza_tasks_100.csv"), "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    tasks = [{"id": str(i+1), "input": row["input"], "output": row["output"]} for i, row in enumerate(reader)]

class UserSession(BaseModel):
    username: str

class Answer(BaseModel):
    task_id: str = Field(..., alias="task_id")
    answer: str
    session_id: str

sessions = {}

def create_session(username):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = f"{username}_{timestamp}"
    filename = f"results_{session_id}.csv"
    sessions[session_id] = {
        "filename": filename,
        "current_task_index": 0,
        "username": username  # ユーザー名を保存
    }
    
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Task ID', 'Instruction', 'Answer', 'Score', 'Reason'])
    
    return session_id

def save_result_to_csv(session_id, task_id, instruction, answer, score, reason):
    filename = sessions[session_id]["filename"]
    with open(filename, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([task_id, instruction, answer, score, reason])

def get_average_score(session_id):
    filename = sessions[session_id]["filename"]
    scores = []
    with open(filename, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            scores.append(float(row['Score']))
    return sum(scores) / len(scores) if scores else 0

@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(10))
def evaluate(pred, input_text, output_text, eval_aspect, model):
    if not pred:
        return {"reason": "No response", "grade": 1}

    prompt = template_prompt.format(
        input_text=input_text,
        output_text=output_text,
        eval_aspect=eval_aspect,
        pred=pred,
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are an AI assistant that evaluates answers to given tasks."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7,
        max_tokens=500,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0
    )

    result = json.loads(response.choices[0].message.content)

    if "reason" not in result or "grade" not in result:
        raise ValueError("Invalid response format")

    if not isinstance(result["grade"], int) or result["grade"] < 1 or result["grade"] > 5:
        raise ValueError("Invalid grade value")

    return result

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open(os.path.join(os.path.dirname(__file__), "static", "index.html"), "r") as f:
        return f.read()

@app.post("/api/start_session")
async def start_session(user: UserSession):
    session_id = create_session(user.username)
    return {"session_id": session_id}

@app.get("/api/task")
async def get_task(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    current_task_index = sessions[session_id]["current_task_index"]
    if current_task_index >= len(tasks):
        return {"id": "end", "description": "All tasks completed"}
    task = tasks[current_task_index]
    return {"id": task["id"], "description": task["input"]}

@app.post("/api/submit")
async def submit_answer(answer: Answer):
    if answer.session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    current_task_index = sessions[answer.session_id]["current_task_index"]
    task = tasks[current_task_index]
    
    try:
        result = evaluate(
            pred=answer.answer,
            input_text=task["input"],
            output_text=task["output"],
            eval_aspect="回答の正確性と完全性",
            model=GPT_MODEL
        )
        
        # Save result to CSV
        save_result_to_csv(answer.session_id, task["id"], task["input"], answer.answer, result["grade"], result["reason"])
        
        # Move to next task
        sessions[answer.session_id]["current_task_index"] += 1
        next_task = await get_task(answer.session_id)
        
        # Calculate user's average score
        user_average_score = get_average_score(answer.session_id)
        
        return JSONResponse(content={
            "task_id": answer.task_id,
            "score": result["grade"],
            "reason": result["reason"],
            "next_task": next_task,
            "user_average_score": user_average_score
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")

@app.get("/api/results")
async def get_results(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    user_average_score = get_average_score(session_id)
    return {
        "user_score": user_average_score,
        "gpt4_score": 4.5,
        "claude_haiku_score": 4.0,
        "claude_opus_score": 4.7,
        "username": sessions[session_id]["username"]  # ユーザー名を返す
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)