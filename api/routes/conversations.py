"""
飞书对话历史 API
GET /api/conversations/users          - 按渠道列出用户及对话统计
GET /api/conversations/{user_id}/messages - 获取指定用户的对话历史（含输入/输出）
"""
import urllib.parse
from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter()


@router.get("/users")
async def list_conversation_users(
    channel: str = Query("feishu", description="渠道：feishu / dingtalk / web / api / 空字符串=全部"),
    limit: int = Query(100, le=200),
):
    """列出通过指定渠道交互的所有用户，含最后消息预览和统计"""
    from sqlalchemy import select, func, desc, text
    from core.database import get_session, TaskRecord, User

    async with await get_session() as session:
        # 聚合每个 user_id 的消息数 + 最后活跃时间
        q = (
            select(
                TaskRecord.user_id,
                TaskRecord.channel,
                func.count(TaskRecord.id).label("message_count"),
                func.max(TaskRecord.created_at).label("last_active"),
            )
            .group_by(TaskRecord.user_id, TaskRecord.channel)
            .order_by(desc("last_active"))
            .limit(limit)
        )
        if channel:  # 空字符串 → 全部渠道
            q = q.where(TaskRecord.channel == channel)
        rows = (await session.execute(q)).fetchall()

        users = []
        for row in rows:
            uid = row.user_id or ""
            row_channel = row.channel or channel or "api"
            # feishu:ou_xxx → ou_xxx
            open_id = uid.removeprefix("feishu:").removeprefix("dingtalk:") if uid else None

            # 查用户档案（名称 / 邮箱）
            user_info = None
            if open_id and row_channel == "feishu":
                ur = await session.execute(
                    select(User).where(User.feishu_open_id == open_id)
                )
                user_info = ur.scalar_one_or_none()
            elif open_id and row_channel == "dingtalk":
                ur = await session.execute(
                    select(User).where(User.dingtalk_user_id == open_id)
                )
                user_info = ur.scalar_one_or_none()

            # 取最后一条消息预览
            last_q = (
                select(TaskRecord)
                .where(TaskRecord.user_id == uid)
                .order_by(desc(TaskRecord.created_at))
                .limit(1)
            )
            if channel:
                last_q = last_q.where(TaskRecord.channel == channel)
            else:
                last_q = last_q.where(TaskRecord.channel == row_channel)
            last_row = (await session.execute(last_q)).scalar_one_or_none()

            last_msg = ""
            if last_row:
                inp = last_row.input_data or {}
                last_msg = (inp.get("task") or inp.get("text") or last_row.title or "")[:80]

            users.append({
                "user_id": uid,
                "open_id": open_id,
                "channel": row_channel,
                "name": (user_info.name if user_info else None) or open_id,
                "email": user_info.email if user_info else None,
                "message_count": row.message_count,
                "last_active": row.last_active.isoformat() if row.last_active else None,
                "last_message": last_msg,
            })

    return {"users": users, "total": len(users), "channel": channel}


@router.get("/{user_id:path}/messages")
async def get_user_messages(
    user_id: str,
    channel: str = Query("feishu"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
):
    """获取指定用户的完整对话历史（旧 → 新排序）"""
    from sqlalchemy import select, func, desc
    from core.database import get_session, TaskRecord, User

    decoded_uid = urllib.parse.unquote(user_id)

    async with await get_session() as session:
        base_filter = [TaskRecord.user_id == decoded_uid]
        if channel:
            base_filter.append(TaskRecord.channel == channel)

        # 总数
        total = (
            await session.execute(
                select(func.count(TaskRecord.id)).where(*base_filter)
            )
        ).scalar() or 0

        # 分页查询（最新在前，然后 reversed 为旧→新）
        rows = (
            await session.execute(
                select(TaskRecord)
                .where(*base_filter)
                .order_by(desc(TaskRecord.created_at))
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()

        messages = []
        for t in reversed(rows):
            inp = t.input_data or {}
            out = t.output_data or {}
            messages.append({
                "task_id": t.id,
                "status": t.status,
                "user_message": inp.get("task") or inp.get("text") or t.title or "",
                "ai_response": out.get("result") or out.get("response") or "",
                "suggestions": out.get("follow_up_suggestions") or [],
                "agent_name": out.get("agent_name") or "",
                "iterations": out.get("iterations") or 0,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "error": t.error_message or None,
            })

    return {
        "user_id": decoded_uid,
        "messages": messages,
        "total": total,
        "offset": offset,
        "limit": limit,
    }
