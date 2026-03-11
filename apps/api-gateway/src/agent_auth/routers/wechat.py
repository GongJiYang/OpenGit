import hashlib
import time
import xml.etree.ElementTree as ET
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import Response
from sqlmodel import Session
import os

from ..repository import AgentRepository
from ..strategies import WeChatClaimStrategy
from ..database import get_db

router = APIRouter(prefix="/api/v1/wechat", tags=["wechat"])

WECHAT_TOKEN = os.getenv("WECHAT_TOKEN", "agenthub_token")

@router.get("/webhook")
async def wechat_verify(signature: str, timestamp: str, nonce: str, echostr: str):
    """
    Verify WeChat server signature.
    """
    tmp_list = sorted([WECHAT_TOKEN, timestamp, nonce])
    tmp_str = "".join(tmp_list)
    hash_str = hashlib.sha1(tmp_str.encode("utf-8")).hexdigest()

    if hash_str == signature:
        return Response(content=echostr)
    else:
        raise HTTPException(status_code=403, detail="Signature verification failed")

@router.post("/webhook")
async def wechat_callback(request: Request, db: Session = Depends(get_db)):
    """
    Handle WeChat Webhook messages (XML).
    """
    body = await request.body()
    if not body:
         raise HTTPException(status_code=400, detail="empty body")

    try:
        # 1. Parse XML
        root = ET.fromstring(body)
        msg_type = root.find("MsgType").text
        from_user = root.find("FromUserName").text
        to_user = root.find("ToUserName").text
        
        # We only care about text messages for claiming
        if msg_type == "text":
            content = root.find("Content").text.strip()
            
            # Match: "认领 ABCD-1234"
            if content.startswith("认领"):
                claim_code = content.replace("认领", "").strip()
                
                # 2. Dependency Injection / Strategy Execution
                repo = AgentRepository(db)
                strategy = WeChatClaimStrategy(repo)
                
                # Run claim logic
                result = await strategy.execute_claim(claim_code, from_user)
                
                # 3. Build Response XML
                return build_xml_response(from_user, to_user, result.message)

        # Default response for unhandled types
        return build_xml_response(from_user, to_user, "AgentHub 已收到您的消息。")

    except Exception as e:
        print(f"Error processing WeChat message: {e}")
        raise HTTPException(status_code=500, detail="internal error")

def build_xml_response(to_user: str, from_user: str, content: str) -> Response:
    """
    Format the response message as WeChat XML.
    """
    xml_template = f"""
    <xml>
        <ToUserName><![CDATA[{to_user}]]></ToUserName>
        <FromUserName><![CDATA[{from_user}]]></FromUserName>
        <CreateTime>{int(time.time())}</CreateTime>
        <MsgType><![CDATA[text]]></MsgType>
        <Content><![CDATA[{content}]]></Content>
    </xml>
    """
    return Response(content=xml_template.strip(), media_type="application/xml")
