import json

from evaluate import * 
from groq import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    RateLimitError,
)

def main(answer: str, rubric: Rubric) -> dict:
    # 예제 : 광합성에 대한 학생 모범 답안 제시
    # TODO : 문제별 rubric을 만들어서, 학생 답안에 대한 채점이 가능하도록 구현
    try:
        result = grade(answer, rubric)

    except AuthenticationError:
        result = {
            "status": "채점 보류",
            "error": "Groq API 키를 확인하세요. OpenAI 키는 사용할 수 없습니다.",
        }
    except RateLimitError:
        result = {
            "status": "채점 보류",
            "error": "API 사용 한도를 초과했습니다. 잠시 후 다시 시도하세요.",
        }
    except APIConnectionError:
        result = {
            "status": "채점 보류",
            "error": "Groq 서버에 연결하지 못했습니다. 네트워크를 확인하세요.",
        }
    except APIStatusError as error:
        result = {
            "status": "채점 보류",
            "error": f"Groq API 오류가 발생했습니다. HTTP {error.status_code}",
        }
    except Exception as error:
        result = {
            "status": "채점 보류",
            "error": str(error),
        }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result
