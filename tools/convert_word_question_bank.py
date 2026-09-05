"""Convert the supplied 100-question Word bank into the web quiz format.

This converter is intentionally scoped to the source document
"高考文言实词与课内教材例句结合练习100题.docx".  It keeps the original
Word file untouched and preserves raw text for later manual auditing.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from docx import Document


HEADER_RE = re.compile(r"^\s*(\d+)\s*[.．]\s*【([^】]+)】(.*)$")
OPTION_RE = re.compile(r"^\s*([A-E])(?:[.．、:：]|\s)?\s*(.*)$")
ANSWER_RE = re.compile(
    r"^\s*(\d+)\s*[.．]?\s*(?:答\s*)?([A-E])\s*(.*)$"
)
FULLWIDTH_OPTIONS = str.maketrans("ＡＢＣＤＥ", "ABCDE")
SUBITEM_RE = re.compile(r"^\s*([①②③④⑤⑥⑦⑧⑨⑩])\s*(.*)$")


def clean_text(value: str) -> str:
    """Normalize layout whitespace without changing Chinese punctuation."""

    value = value.replace("\u00a0", " ").replace("\u3000", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def read_lines(path: Path) -> list[str]:
    document = Document(str(path))
    lines: list[str] = []
    for paragraph in document.paragraphs:
        # A paragraph can contain manual line breaks, so split them before
        # normalization.  This source mostly uses one logical record per
        # paragraph, but this also handles the exceptional mixed paragraphs.
        for part in paragraph.text.splitlines():
            text = clean_text(part)
            if text:
                lines.append(text)
    return lines


def strip_option_annotation(text: str) -> tuple[str, str | None]:
    """Remove the trailing source-provided meaning from an option.

    The source uses a final full-width or ASCII parenthetical expression for
    the supplied meaning.  Earlier parentheses containing book titles remain
    part of the displayed option because they are not the final annotation.
    """

    match = re.search(r"\s*(?:（([^（）]*)）|\(([^()]*)\))\s*$", text)
    if not match:
        return text.strip(), None
    meaning = (match.group(1) or match.group(2) or "").strip()
    if not meaning:
        return text.strip(), None
    display = text[: match.start()].rstrip()
    return display, meaning


def infer_rule(stem: str) -> str:
    if "意思不同" in stem or "意义不同" in stem:
        return "different_meaning"
    if "意思相同" in stem or "意义相同" in stem or "解释相同" in stem:
        return "same_meaning"
    if "没有错误" in stem or "正确的一项" in stem or "解释正确" in stem:
        return "select_correct"
    if any(token in stem for token in ("错误", "不正确", "有误")):
        return "select_incorrect"
    return "single_choice"


def find_header_positions(lines: list[str]) -> list[tuple[int, int, str, str]]:
    positions: list[tuple[int, int, str, str]] = []
    for index, line in enumerate(lines):
        match = HEADER_RE.match(line)
        if match:
            positions.append((index, int(match.group(1)), match.group(2).strip(), match.group(3).strip()))
    return positions


def parse_answer_map(lines: list[str], start: int) -> dict[int, dict[str, str]]:
    answers: dict[int, dict[str, str]] = {}
    for line in lines[start:]:
        match = ANSWER_RE.match(line)
        if not match:
            continue
        number = int(match.group(1))
        answers[number] = {
            "answer": match.group(2),
            "explanation": match.group(3).strip(),
            "raw": line,
        }
    return answers


def parse_question(
    lines: list[str],
    start: int,
    end: int,
    number: int,
    word: str,
    stem_tail: str,
    answer: dict[str, str],
    source_name: str,
) -> dict[str, Any]:
    options: list[dict[str, Any]] = []
    supporting_items: list[dict[str, Any]] = []
    context_lines: list[str] = []
    raw_lines = lines[start:end]
    for line in lines[start + 1 : end]:
        marker_line = line.translate(FULLWIDTH_OPTIONS)
        if not re.match(r"^\s*[A-E]", marker_line):
            subitem = SUBITEM_RE.match(line)
            if subitem:
                raw_item = subitem.group(2).strip()
                display_text, supplied_meaning = strip_option_annotation(raw_item)
                supporting_items.append(
                    {
                        "key": subitem.group(1),
                        "text": display_text,
                        "meaning": supplied_meaning,
                        "rawText": raw_item,
                    }
                )
            else:
                context_lines.append(line)
            continue

        # Most options occupy one paragraph.  Question 14 is an exceptional
        # packed line: "A①② B③④ C⑤⑥ D⑦⑧".  Split only on an uppercase
        # option marker at the start of the line or after whitespace.
        packed = list(re.finditer(r"(?<!\S)([A-E])", marker_line))
        if len(packed) > 1:
            for index, marker in enumerate(packed):
                next_start = packed[index + 1].start() if index + 1 < len(packed) else len(line)
                key = marker.group(1)
                raw_option = line[marker.end() : next_start].strip()
                raw_option = re.sub(r"^[.．、:：]\s*", "", raw_option)
                display_text, supplied_meaning = strip_option_annotation(raw_option)
                options.append(
                    {
                        "key": key,
                        "text": display_text,
                        "meaning": supplied_meaning,
                        "rawText": raw_option,
                    }
                )
            continue

        match = OPTION_RE.match(marker_line)
        if match:
            key = match.group(1)
            raw_option = match.group(2).strip()
            display_text, supplied_meaning = strip_option_annotation(raw_option)
            options.append(
                {
                    "key": key,
                    "text": display_text,
                    "meaning": supplied_meaning,
                    "rawText": raw_option,
                }
            )

    return {
        "id": f"gaokao-shici-{number:03d}",
        "number": number,
        "type": "single_choice",
        "rule": infer_rule(stem_tail),
        "word": word,
        "stem": clean_text(stem_tail),
        "context": context_lines,
        "supportingItems": supporting_items,
        "options": options,
        "answer": answer["answer"],
        "explanation": answer["explanation"],
        "source": {
            "file": source_name,
            "kind": "provided_compiled_question",
            "scope": "高考范围",
            "section": "高考文言实词与课内教材例句结合练习",
            "originalNumber": number,
        },
        "rawText": raw_lines,
    }


def convert(input_path: Path) -> dict[str, Any]:
    lines = read_lines(input_path)
    headers = find_header_positions(lines)
    if len(headers) != 100:
        raise ValueError(f"expected 100 question headers, found {len(headers)}")

    reference_answer_index = next(
        (index for index, line in enumerate(lines) if line.startswith("附：参考答案")),
        None,
    )
    if reference_answer_index is None:
        raise ValueError("could not find the first answer section marker")

    # The source places answers 1-50 between questions 1-50 and 51-100.
    # The second answer block starts with the first standalone "51.B..." line.
    second_answer_index = next(
        (
            index
            for index in range(headers[-1][0] + 1, len(lines))
            if (match := ANSWER_RE.match(lines[index])) and int(match.group(1)) == 51
        ),
        None,
    )
    if second_answer_index is None:
        raise ValueError("could not find the second answer section")

    answers = parse_answer_map(lines, reference_answer_index + 1)
    missing_answers = [number for number in range(1, 101) if number not in answers]
    if missing_answers:
        raise ValueError(f"missing answers for questions: {missing_answers}")

    questions: list[dict[str, Any]] = []
    for position, (start, number, word, stem_tail) in enumerate(headers):
        if number != position + 1:
            raise ValueError(f"question order mismatch at position {position}: {number}")
        if number < 50:
            end = headers[position + 1][0]
        elif number == 50:
            end = reference_answer_index
        elif number < 100:
            end = headers[position + 1][0]
        else:
            end = second_answer_index
        question = parse_question(
            lines,
            start,
            end,
            number,
            word,
            stem_tail,
            answers[number],
            input_path.name,
        )
        if len(question["options"]) < 2:
            raise ValueError(f"question {number} has fewer than two options")
        if question["answer"] not in {option["key"] for option in question["options"]}:
            raise ValueError(
                f"question {number} answer {question['answer']} is not in its options"
            )
        questions.append(question)

    return {
        "schemaVersion": "1.0",
        "title": "高考文言实词与课内教材例句结合练习100题",
        "description": "由用户提供的 Word 题库转换而来，供限时单选题模式使用。",
        "quizDefaults": {
            "durationSeconds": 120,
            "correctScore": 1,
            "wrongScore": -1,
            "scoring": {
                "mode": "fixed",
                "baseCorrect": 1,
                "baseWrongPenalty": 1,
                "correctStreakAfter": 2,
                "correctStreakScore": 2,
                "wrongStreakAfter": 2,
                "wrongStreakPenalty": 2,
            },
        },
        "source": {
            "file": input_path.name,
            "format": "docx",
            "scope": "高考范围",
            "questionCount": 100,
        },
        "questions": questions,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    result = convert(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    option_counts: dict[int, int] = {}
    for question in result["questions"]:
        count = len(question["options"])
        option_counts[count] = option_counts.get(count, 0) + 1
    print(
        json.dumps(
            {
                "output": str(args.output),
                "questions": len(result["questions"]),
                "optionCountDistribution": option_counts,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
