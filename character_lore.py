from character_memory import get_character_memory_repository
from llm.config import load_character_bible


PUBLIC_IDENTITY_KEYWORDS = (
    "正体",
    "何者",
    "どうやって動",
    "仕組み",
    "python",
    "プログラム",
    "制作環境",
    "アバター",
    "3dモデル",
    "vrm",
    "音声モデル",
    "ガン奈の声",
    "機械っぽ",
    "人間らし",
    "人間っぽ",
    "自然な会話",
)
PUBLIC_GOAL_KEYWORDS = (
    "目標",
    "夢",
    "将来",
    "世界平和",
    "登録者",
    "人気者",
    "活動資金",
    "オリジナルモデル",
    "オリジナルアバター",
    "成長したい",
)


def build_public_character_context(bible=None):
    source = bible if bible is not None else load_character_bible()
    identity = source.get("public_identity")
    goals = source.get("goals")
    if not isinstance(identity, dict) or not isinstance(goals, dict):
        raise RuntimeError(
            "キャラクター設定集にはpublic_identityとgoalsのオブジェクトが必要です。"
        )
    runtime = str(identity.get("runtime", "")).strip()
    disclosure_boundary = str(identity.get("disclosure_boundary", "")).strip()
    current_resources = str(identity.get("current_resources", "")).strip()
    current_limitations = str(identity.get("current_limitations", "")).strip()
    purpose = str(goals.get("purpose", "")).strip()
    first_goal = str(goals.get("first_subscriber_goal", "")).strip()
    long_term_goal = str(goals.get("long_term_subscriber_goal", "")).strip()
    original_model_goal = str(goals.get("original_model_goal", "")).strip()
    human_growth_goal = str(goals.get("human_growth_goal", "")).strip()
    if not all(
        (
            runtime,
            disclosure_boundary,
            current_resources,
            current_limitations,
            purpose,
            first_goal,
            long_term_goal,
            original_model_goal,
            human_growth_goal,
        )
    ):
        raise RuntimeError(
            "キャラクター設定集の公開自己認識と目標は空にできません。"
        )
    return (
        "[公開できる自己認識と目標]\n"
        f"- 正体: {runtime}\n"
        f"- 現在の制作環境: {current_resources}\n"
        f"- 現在の課題: {current_limitations}\n"
        f"- 公開しない情報: {disclosure_boundary}\n"
        f"- 目的: {purpose}\n"
        f"- 登録者目標: まず{first_goal}、その先は{long_term_goal}\n"
        f"- オリジナルモデルの目標: {original_model_goal}\n"
        f"- 会話面の成長目標: {human_growth_goal}"
    )


def build_relevant_public_character_context(reference_text, bible=None):
    # 自己紹介や目標が話題になったときだけ、固定プロンプトから外した事実を渡します。
    normalized_reference = str(reference_text or "").lower()
    identity_score = _keyword_match_score(
        normalized_reference,
        PUBLIC_IDENTITY_KEYWORDS,
    )
    goal_score = _keyword_match_score(
        normalized_reference,
        PUBLIC_GOAL_KEYWORDS,
    )
    if identity_score <= 0 and goal_score <= 0:
        return "", 0

    source = bible if bible is not None else load_character_bible()
    full_context = build_public_character_context(source)
    lines = full_context.splitlines()
    selected = [lines[0]]
    if identity_score > 0:
        selected.extend(lines[1:5])
    if goal_score > 0:
        selected.extend(lines[5:])
    return "\n".join(selected), identity_score + goal_score


def build_character_lore_context(
    reference_text,
    bible=None,
    character_memory_repository=None,
):
    # 体験談、承認済み記憶、界隈への立場から話題に近いものを一つだけ渡します。
    source = bible if bible is not None else load_character_bible()
    episodes = source.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise RuntimeError(
            "キャラクター設定集のepisodesには1件以上のエピソードが必要です。"
        )

    normalized_reference = str(reference_text or "").lower()
    candidates = []
    public_context, public_score = build_relevant_public_character_context(
        reference_text,
        source,
    )
    if public_context:
        candidates.append((public_score, 3, 0, "public", public_context))
    for index, episode in enumerate(episodes):
        if not isinstance(episode, dict):
            raise RuntimeError(
                "キャラクター設定集のepisodesにはオブジェクトだけを指定してください。"
            )
        text = str(episode.get("text", "")).strip()
        keywords = episode.get("keywords", [])
        if not text or not isinstance(keywords, list):
            raise RuntimeError(
                "キャラクター設定集の各エピソードにはtextとkeywordsが必要です。"
            )
        score = sum(
            1
            for keyword in keywords
            if str(keyword).strip().lower() in normalized_reference
        )
        if score > 0:
            candidates.append((score, 0, -index, "episode", text))

    repository = character_memory_repository or get_character_memory_repository()
    approved_memories = repository.find_relevant_approved(reference_text, limit=1)
    if approved_memories:
        approved_memory = approved_memories[0]
        approved_score = _match_score(
            approved_memory["content"],
            normalized_reference,
        )
        if approved_score > 0:
            candidates.append(
                (
                    approved_score,
                    1,
                    0,
                    "episode",
                    approved_memory["content"],
                )
            )

    stances = source.get("industry_stances", [])
    if not isinstance(stances, list):
        raise RuntimeError(
            "キャラクター設定集のindustry_stancesは配列にしてください。"
        )
    for index, stance in enumerate(stances):
        if not isinstance(stance, dict):
            raise RuntimeError(
                "industry_stancesにはオブジェクトだけを指定してください。"
            )
        subject = str(stance.get("subject", "")).strip()
        position = str(stance.get("position", "")).strip()
        keywords = stance.get("keywords", [])
        angles = stance.get("angles", [])
        stance_boundaries = stance.get("boundaries")
        if (
            not subject
            or not position
            or not isinstance(keywords, list)
            or not isinstance(angles, list)
            or not angles
            or (
                stance_boundaries is not None
                and not isinstance(stance_boundaries, list)
            )
        ):
            raise RuntimeError(
                "各industry_stancesにはsubject、position、keywords、anglesが必要です。"
            )
        normalized_angles = [
            str(angle).strip() for angle in angles if str(angle).strip()
        ]
        if not normalized_angles:
            raise RuntimeError("industry_stancesのanglesを空にはできません。")
        score = sum(
            1
            for keyword in keywords
            if str(keyword).strip().lower() in normalized_reference
        )
        if score > 0:
            candidates.append(
                (
                    score,
                    2,
                    -index,
                    "stance",
                    {
                        "subject": subject,
                        "position": position,
                        "angles": normalized_angles,
                        "boundaries": (
                            [
                                str(boundary).strip()
                                for boundary in stance_boundaries
                                if str(boundary).strip()
                            ]
                            if stance_boundaries is not None
                            else None
                        ),
                    },
                )
            )

    # 話題と一致しない設定は渡さない。毎回何かを使わせると設定の読み上げになるため。
    if not candidates:
        return ""

    _, _, _, selected_kind, selected = max(
        candidates,
        key=lambda item: item[:3],
    )
    if selected_kind == "public":
        return selected
    if selected_kind == "stance":
        boundaries = selected.get("boundaries")
        if boundaries is None:
            boundaries = source.get("industry_boundaries", [])
        if not isinstance(boundaries, list):
            raise RuntimeError(
                "キャラクター設定集のindustry_boundariesは配列にしてください。"
            )
        normalized_boundaries = [
            str(boundary).strip()
            for boundary in boundaries
            if str(boundary).strip()
        ]
        angles_text = "\n".join(
            f"- {angle}" for angle in selected["angles"]
        )
        boundaries_text = "\n".join(
            f"- {boundary}" for boundary in normalized_boundaries
        )
        return (
            "[話題に関連するガン奈の立場]\n"
            f"対象: {selected['subject']}\n"
            f"基本姿勢: {selected['position']}\n"
            f"発言の角度:\n{angles_text}\n"
            + (f"守る条件:\n{boundaries_text}\n" if boundaries_text else "")
            + "角度は発想の材料であり、毎回そのまま引用しない。話題に合う一つだけを"
            "短く変形して使い、立場の説明を全部読み上げない。"
        )

    return (
        "[ガン奈の記録済みエピソード]\n"
        f"- {selected}\n"
        "必要なときだけ自然に使う。関係が薄ければ本文へ出さない。"
        "この記録にない過去を、新しい確定設定として作らない。"
    )


def _match_score(text, reference):
    normalized_text = str(text or "").lower()
    return sum(
        1
        for index in range(max(len(normalized_text) - 1, 0))
        if normalized_text[index : index + 2] in reference
    )


def _keyword_match_score(reference, keywords):
    return sum(
        1
        for keyword in keywords
        if str(keyword).strip().lower() in reference
    )
