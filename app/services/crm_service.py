import os
import json
import logging
import httpx
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode, urlparse, parse_qs
from sqlalchemy.orm import Session
from cryptography.fernet import Fernet

from models import CRMIntegration, CRMRecording, User
from database import SessionLocal

logger = logging.getLogger(__name__)

# Ключ шифрования для токенов (в проде должен быть в переменных окружения)
ENCRYPTION_KEY = os.getenv("CRM_ENCRYPTION_KEY", Fernet.generate_key().decode())
cipher_suite = Fernet(ENCRYPTION_KEY.encode())


class CRMService:
    """Базовый класс для интеграции с CRM системами"""
    
    def __init__(self, integration: CRMIntegration):
        self.integration = integration
        self.access_token = self._decrypt_token(integration.access_token) if integration.access_token else None
        self.refresh_token = self._decrypt_token(integration.refresh_token) if integration.refresh_token else None
    
    def _encrypt_token(self, token: str) -> str:
        """Шифрует токен для безопасного хранения"""
        return cipher_suite.encrypt(token.encode()).decode()
    
    def _decrypt_token(self, encrypted_token: str) -> str:
        """Расшифровывает токен"""
        return cipher_suite.decrypt(encrypted_token.encode()).decode()
    
    def save_tokens(self, db: Session, access_token: str, refresh_token: Optional[str] = None, expires_in: Optional[int] = None):
        """Сохраняет токены в БД"""
        self.integration.access_token = self._encrypt_token(access_token)
        if refresh_token:
            self.integration.refresh_token = self._encrypt_token(refresh_token)
        if expires_in:
            self.integration.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        self.integration.updated_at = datetime.utcnow()
        db.commit()
        
        # Обновляем локальные токены
        self.access_token = access_token
        self.refresh_token = refresh_token
    
    async def get_recordings(self, db: Session, limit: int = 100) -> List[Dict[str, Any]]:
        """Получить список записей из CRM"""
        raise NotImplementedError("Subclasses must implement this method")
    
    async def download_recording(self, recording_url: str, save_path: str) -> bool:
        """Скачать запись"""
        raise NotImplementedError("Subclasses must implement this method")


class AmoCRMService(CRMService):
    """Сервис для интеграции с AmoCRM"""
    
    OAUTH_URL = "https://www.amocrm.ru/oauth"
    API_VERSION = "/api/v4"
    
    def __init__(self, integration: CRMIntegration):
        super().__init__(integration)
        self.base_url = f"https://{integration.crm_domain}.amocrm.ru{self.API_VERSION}" if integration.crm_domain else None
    
    def get_oauth_url(self, client_id: str, redirect_uri: str, state: str) -> str:
        """Получить URL для OAuth авторизации"""
        params = {
            "client_id": client_id,
            "state": state,
            "redirect_uri": redirect_uri,
            "response_type": "code",
        }
        return f"{self.OAUTH_URL}?{urlencode(params)}"
    
    async def exchange_code_for_tokens(self, code: str, redirect_uri: str, client_id: str, client_secret: str, domain: str) -> Dict[str, Any]:
        """Обменять код на токены"""
        url = f"https://{domain}.amocrm.ru/oauth2/access_token"
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data)
            response.raise_for_status()
            return response.json()
    
    async def refresh_access_token(self, db: Session) -> bool:
        """Обновить access token используя refresh token"""
        if not self.refresh_token or not self.integration.crm_domain:
            return False
        
        redirect_uri = self.integration.webhook_url or os.getenv(
            "AMOCRM_REDIRECT_URI", "https://up-stat.com/crm/oauth/callback"
        )
        
        url = f"https://{self.integration.crm_domain}.amocrm.ru/oauth2/access_token"
        data = {
            "client_id": self._decrypt_token(self.integration.client_id),
            "client_secret": self._decrypt_token(self.integration.client_secret),
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "redirect_uri": redirect_uri,
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=data)
                response.raise_for_status()
                token_data = response.json()
                
                self.save_tokens(
                    db,
                    access_token=token_data["access_token"],
                    refresh_token=token_data.get("refresh_token"),
                    expires_in=token_data.get("expires_in")
                )
                return True
        except Exception as e:
            logger.error(f"Failed to refresh token for AmoCRM: {e}")
            return False
    
    async def _make_api_request(self, endpoint: str, method: str = "GET", **kwargs) -> Optional[Dict[str, Any]]:
        """Выполнить запрос к API с автообновлением токена"""
        if not self.access_token or not self.base_url:
            return None
        
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        url = f"{self.base_url}{endpoint}"
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    # Токен истек, пробуем обновить
                    db = SessionLocal()
                    try:
                        if await self.refresh_access_token(db):
                            # Повторяем запрос с новым токеном
                            headers["Authorization"] = f"Bearer {self.access_token}"
                            response = await client.request(method, url, headers=headers, **kwargs)
                            response.raise_for_status()
                            return response.json()
                    finally:
                        db.close()
                logger.error(f"API request failed: {e}")
                return None
    
    MIN_CALL_DURATION = 35

    async def get_recordings(self, db: Session, limit: int = 100) -> List[Dict[str, Any]]:
        """Получить список записей звонков из AmoCRM (из /calls, примечаний к сделкам и контактам)"""
        seen_ids: set = set()
        recordings: List[Dict[str, Any]] = []
        date_from = (datetime.utcnow() - timedelta(days=30)).timestamp()
        skipped_short = 0

        # 1) Звонки из ресурса /calls (телефония/виджет) — пагинация
        logger.info("[AmoCRM] Fetching calls from /calls endpoint...")
        page = 1
        calls_count = 0
        calls_with_link = 0
        while True:
            calls_data = await self._make_api_request(
                "/calls",
                params={
                    "filter[created_at][from]": int(date_from),
                    "limit": 250,
                    "page": page,
                    "with": "call_result"
                }
            )
            if not calls_data or "_embedded" not in calls_data:
                if page == 1:
                    logger.info(f"[AmoCRM] /calls returned: {json.dumps(calls_data or {}, ensure_ascii=False)[:500]}")
                break
            calls_list = calls_data["_embedded"].get("calls", [])
            if not calls_list:
                break
            calls_count += len(calls_list)
            for call in calls_list:
                if not call.get("link"):
                    continue
                calls_with_link += 1
                duration = call.get("duration", 0)
                if duration < self.MIN_CALL_DURATION:
                    skipped_short += 1
                    continue
                cid = str(call["id"])
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                contact_info = await self._get_contact_info(call.get("contact_id"))
                recordings.append({
                    "crm_record_id": cid,
                    "crm_call_id": cid,
                    "call_date": datetime.fromtimestamp(call["created_at"]),
                    "duration_seconds": duration,
                    "direction": "inbound" if call.get("direction") == "inbound" else "outbound",
                    "recording_url": call["link"],
                    "manager_name": call.get("responsible_user_name", ""),
                    "client_name": contact_info.get("name", ""),
                    "client_phone": call.get("phone", ""),
                    "client_company": contact_info.get("company", ""),
                    "crm_metadata": {
                        "source": call.get("source", ""),
                        "call_result": call.get("call_result", ""),
                        "call_status": call.get("call_status", ""),
                        "note": call.get("note", "")
                    }
                })
            if len(calls_list) < 250:
                break
            page += 1
        logger.info(f"[AmoCRM] /calls: total={calls_count}, with_link={calls_with_link}, "
                     f"short(<{self.MIN_CALL_DURATION}s)={skipped_short}, added={len(recordings)}")

        # 2) Звонки из примечаний к сделкам (leads/notes: call_in, call_out)
        notes_recordings = await self._get_recordings_from_notes("leads", db, date_from, seen_ids)
        recordings.extend(notes_recordings)
        logger.info(f"[AmoCRM] leads/notes: added={len(notes_recordings)}")

        # 3) Звонки из примечаний к контактам (contacts/notes: call_in, call_out)
        notes_contacts = await self._get_recordings_from_notes("contacts", db, date_from, seen_ids)
        recordings.extend(notes_contacts)
        logger.info(f"[AmoCRM] contacts/notes: added={len(notes_contacts)}")

        logger.info(f"[AmoCRM] Total recordings collected: {len(recordings)}")
        recordings.sort(key=lambda r: r["call_date"], reverse=True)
        return recordings

    async def _get_recordings_from_notes(
        self, entity_type: str, db: Session, date_from: float, seen_ids: set
    ) -> List[Dict[str, Any]]:
        """Получить записи звонков из примечаний (call_in, call_out) к сделкам или контактам."""
        result: List[Dict[str, Any]] = []
        page = 1
        per_page = 250
        skipped_short = 0

        while True:
            params_list: List[tuple] = [
                ("filter[updated_at][from]", int(date_from)),
                ("limit", per_page),
                ("page", page),
                ("filter[note_type][]", "call_in"),
                ("filter[note_type][]", "call_out"),
            ]

            notes_data = await self._make_api_request(f"/{entity_type}/notes", params=params_list)

            if page == 1:
                if not notes_data or "_embedded" not in notes_data:
                    logger.info(f"[AmoCRM] /{entity_type}/notes returned no _embedded. Raw: {json.dumps(notes_data or {}, ensure_ascii=False)[:500]}")
                else:
                    total_notes = len(notes_data["_embedded"].get("notes", []))
                    logger.info(f"[AmoCRM] /{entity_type}/notes page 1: {total_notes} notes found")

            if not notes_data or "_embedded" not in notes_data:
                break

            notes = notes_data["_embedded"].get("notes", [])
            if not notes:
                break

            for note in notes:
                params_note = note.get("params") or {}
                link = params_note.get("link")
                if not link or not str(link).strip():
                    continue
                link = str(link).strip()
                duration = int(params_note.get("duration") or 0)
                if duration < self.MIN_CALL_DURATION:
                    skipped_short += 1
                    continue
                crm_record_id = f"{entity_type}_note_{note.get('entity_id', 0)}_{note['id']}"
                if crm_record_id in seen_ids:
                    continue
                seen_ids.add(crm_record_id)

                manager_name = ""
                if note.get("responsible_user_id"):
                    manager_name = await self._get_user_name(note["responsible_user_id"]) or ""
                if not manager_name and isinstance(params_note.get("call_responsible"), str):
                    manager_name = params_note["call_responsible"]

                entity_name = ""
                entity_id = note.get("entity_id")
                if entity_type == "leads" and entity_id:
                    entity_name = await self._get_lead_name(entity_id) or ""
                elif entity_type == "contacts" and entity_id:
                    ci = await self._get_contact_info(entity_id)
                    entity_name = ci.get("name", "")

                result.append({
                    "crm_record_id": crm_record_id,
                    "crm_call_id": str(note["id"]),
                    "call_date": datetime.fromtimestamp(note["created_at"]),
                    "duration_seconds": duration,
                    "direction": "inbound" if note.get("note_type") == "call_in" else "outbound",
                    "recording_url": link,
                    "manager_name": manager_name,
                    "client_name": entity_name,
                    "client_phone": params_note.get("phone") or "",
                    "client_company": "",
                    "crm_metadata": {
                        "source": params_note.get("source", ""),
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "note_type": note.get("note_type", ""),
                    }
                })

            if len(notes) < per_page:
                break
            page += 1

        if skipped_short:
            logger.info(f"[AmoCRM] /{entity_type}/notes: skipped {skipped_short} short calls (<{self.MIN_CALL_DURATION}s)")
        return result

    async def debug_api(self) -> Dict[str, Any]:
        """Диагностический метод: показывает что возвращает AmoCRM API по звонкам."""
        result: Dict[str, Any] = {"domain": self.integration.crm_domain, "has_token": bool(self.access_token)}
        date_from = int((datetime.utcnow() - timedelta(days=30)).timestamp())

        # /calls
        calls_data = await self._make_api_request("/calls", params={"limit": 5})
        if calls_data and "_embedded" in calls_data:
            calls = calls_data["_embedded"].get("calls", [])
            result["calls_endpoint"] = {"count": len(calls), "sample": calls[:2]}
        else:
            result["calls_endpoint"] = {"raw": calls_data}

        # leads/notes (call_in, call_out)
        leads_notes = await self._make_api_request("/leads/notes", params=[
            ("limit", 5),
            ("filter[note_type][]", "call_in"),
            ("filter[note_type][]", "call_out"),
        ])
        if leads_notes and "_embedded" in leads_notes:
            notes = leads_notes["_embedded"].get("notes", [])
            result["leads_notes"] = {"count": len(notes), "sample": notes[:2]}
        else:
            result["leads_notes"] = {"raw": leads_notes}

        # contacts/notes (call_in, call_out)
        contacts_notes = await self._make_api_request("/contacts/notes", params=[
            ("limit", 5),
            ("filter[note_type][]", "call_in"),
            ("filter[note_type][]", "call_out"),
        ])
        if contacts_notes and "_embedded" in contacts_notes:
            notes = contacts_notes["_embedded"].get("notes", [])
            result["contacts_notes"] = {"count": len(notes), "sample": notes[:2]}
        else:
            result["contacts_notes"] = {"raw": contacts_notes}

        # leads list (just to see if we have access)
        leads_data = await self._make_api_request("/leads", params={"limit": 3, "with": "contacts"})
        if leads_data and "_embedded" in leads_data:
            leads = leads_data["_embedded"].get("leads", [])
            result["leads_access"] = {"count": len(leads), "sample_names": [l.get("name") for l in leads[:3]]}
        else:
            result["leads_access"] = {"raw": leads_data}

        return result

    async def _get_lead_name(self, lead_id: int) -> Optional[str]:
        """Получить название сделки по ID."""
        data = await self._make_api_request(f"/leads/{lead_id}")
        if data:
            return data.get("name") or ""
        return None

    async def _get_user_name(self, user_id: int) -> Optional[str]:
        """Получить имя пользователя по ID."""
        data = await self._make_api_request(f"/users/{user_id}")
        if not data:
            return None
        name = (data.get("name") or "").strip()
        if name:
            return name
        first = data.get("first_name") or ""
        last = data.get("last_name") or ""
        return f"{first} {last}".strip() or None
    
    async def _get_contact_info(self, contact_id: Optional[int]) -> Dict[str, Any]:
        """Получить информацию о контакте"""
        if not contact_id:
            return {}
        
        contact_data = await self._make_api_request(f"/contacts/{contact_id}")
        if not contact_data:
            return {}
        
        contact = contact_data
        info = {
            "name": contact.get("name", ""),
            "company": ""
        }
        
        # Получаем компанию контакта
        if contact.get("_embedded", {}).get("companies"):
            company_id = contact["_embedded"]["companies"][0]["id"]
            company_data = await self._make_api_request(f"/companies/{company_id}")
            if company_data:
                info["company"] = company_data.get("name", "")
        
        return info
    
    async def download_recording(self, recording_url: str, save_path: str) -> bool:
        """Скачать запись звонка"""
        try:
            headers = {
                "Authorization": f"Bearer {self.access_token}"
            }
            
            async with httpx.AsyncClient(timeout=300.0) as client:  # 5 минут таймаут
                # Создаем директорию если не существует
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                
                # Стриминговое скачивание для больших файлов
                async with client.stream("GET", recording_url, headers=headers) as response:
                    response.raise_for_status()
                    
                    with open(save_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            f.write(chunk)
                
                return True
        except Exception as e:
            logger.error(f"Failed to download recording: {e}")
            return False


class Bitrix24WebhookService(CRMService):
    """Сервис для интеграции с Bitrix24 через вебхуки (входящие вебхуки)"""
    
    def __init__(self, integration: CRMIntegration):
        super().__init__(integration)
        # Для вебхука токен хранится в access_token, а базовый URL извлекается из него
        if integration.access_token:
            webhook_url = self._decrypt_token(integration.access_token)
            # URL формата: https://domain.bitrix24.ru/rest/1/abc123.../
            self.webhook_url = webhook_url.rstrip('/')
            # Извлекаем домен из URL для использования в других местах
            if 'bitrix24' in webhook_url:
                domain = webhook_url.split('//')[1].split('/')[0]
                integration.crm_domain = domain
        else:
            self.webhook_url = None
    
    async def _make_api_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Выполнить запрос к REST API Bitrix24 через вебхук"""
        if not self.webhook_url:
            return None
        
        url = f"{self.webhook_url}/{method}.json"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                has_complex_params = params and any(isinstance(v, (dict, list)) for v in params.values())
                if has_complex_params:
                    response = await client.post(url, json=params)
                else:
                    response = await client.get(url, params=params or {})
                response.raise_for_status()
                data = response.json()
                
                if "error" in data:
                    logger.error(f"Bitrix24 Webhook API error: {data}")
                    return None
                
                return data
            except Exception as e:
                logger.error(f"Bitrix24 Webhook API request failed: {e}")
                return None
    
    async def get_recordings(self, db: Session, limit: int = 100) -> List[Dict[str, Any]]:
        """Получить список записей звонков из Bitrix24 (Webhook)"""
        return await self._bitrix24_get_recordings()

    def _get_auth_token(self) -> str:
        """Извлечь auth-токен для подстановки в URL файлов Bitrix24"""
        if hasattr(self, 'webhook_url') and self.webhook_url:
            parts = self.webhook_url.rstrip('/').split('/')
            return parts[-1] if parts else ""
        if hasattr(self, 'access_token') and self.access_token:
            return self.access_token
        at = getattr(self.integration, 'access_token', None)
        if at:
            try:
                return self._decrypt_token(at)
            except Exception:
                pass
        return ""

    MIN_CALL_DURATION = 35

    async def _bitrix24_get_recordings(self) -> List[Dict[str, Any]]:
        """Универсальный метод получения звонков из Bitrix24 (activity + voximplant)"""
        recordings: List[Dict[str, Any]] = []
        seen_ids: set = set()
        date_from = (datetime.utcnow() - timedelta(days=30)).isoformat()
        auth_token = self._get_auth_token()

        # --- Шаг 0: попробуем загрузить voximplant-статистику для длительности ---
        voxi_duration_map: Dict[str, int] = {}
        voxi_url_map: Dict[str, str] = {}
        try:
            voxi_data = await self._make_api_request(
                "voximplant.statistic.get",
                params={
                    "FILTER": {">CALL_START_DATE": date_from},
                    "LIMIT": 500,
                }
            )
            if voxi_data and "result" in voxi_data:
                voxi_calls = voxi_data["result"]
                logger.info(f"[Bitrix24] voximplant cache: {len(voxi_calls)} entries loaded")
                for vc in voxi_calls:
                    vid = str(vc.get("CALL_ID", ""))
                    if vid:
                        voxi_duration_map[vid] = int(vc.get("CALL_DURATION", 0))
                        rec = vc.get("RECORD_URL") or vc.get("CALL_RECORD_URL") or ""
                        if rec:
                            voxi_url_map[vid] = rec
                    phone = vc.get("PHONE_NUMBER", "")
                    ts = vc.get("CALL_START_DATE", "")
                    if phone and ts:
                        key = f"{phone}_{ts[:16]}"
                        voxi_duration_map[key] = int(vc.get("CALL_DURATION", 0))
                        rec = vc.get("RECORD_URL") or vc.get("CALL_RECORD_URL") or ""
                        if rec:
                            voxi_url_map[key] = rec
            else:
                logger.info("[Bitrix24] voximplant.statistic.get unavailable or empty")
        except Exception as e:
            logger.warning(f"[Bitrix24] voximplant cache error: {e}")

        # --- Шаг 1: crm.activity.list TYPE_ID=2 ---
        logger.info("[Bitrix24] Fetching crm.activity.list TYPE_ID=2...")
        start = 0
        all_calls = []
        while True:
            calls_data = await self._make_api_request(
                "crm.activity.list",
                params={
                    "filter": {"TYPE_ID": 2, ">START_TIME": date_from},
                    "select": [
                        "ID", "OWNER_ID", "OWNER_TYPE_ID", "SUBJECT",
                        "START_TIME", "END_TIME", "DESCRIPTION", "DIRECTION",
                        "RESPONSIBLE_ID", "STORAGE_ELEMENT_IDS", "FILES",
                        "PROVIDER_ID", "PROVIDER_TYPE_ID", "PROVIDER_DATA",
                    ],
                    "order": {"START_TIME": "DESC"},
                    "start": start,
                }
            )
            if not calls_data or "result" not in calls_data:
                logger.info(f"[Bitrix24] crm.activity.list no result: "
                            f"{json.dumps(calls_data or {}, ensure_ascii=False)[:500]}")
                break
            batch = calls_data["result"]
            if not batch:
                break
            all_calls.extend(batch)
            nxt = calls_data.get("next")
            if nxt is None:
                break
            start = nxt

        logger.info(f"[Bitrix24] Found {len(all_calls)} call activities")
        if all_calls:
            logger.info(f"[Bitrix24] Sample activity: "
                        f"{json.dumps(all_calls[0], ensure_ascii=False, default=str)[:800]}")

        skipped_no_file = 0
        skipped_missed = 0
        skipped_short = 0

        for call in all_calls:
            try:
                call_id = str(call.get("ID", ""))
                if call_id in seen_ids:
                    continue
                seen_ids.add(call_id)

                subject = (call.get("SUBJECT") or "").lower()
                is_missed = ("пропущенн" in subject or "missed" in subject
                             or "неотвеч" in subject or "не отвеч" in subject)
                if is_missed:
                    skipped_missed += 1
                    continue

                record_url = await self._extract_recording_url(call, auth_token)

                if not record_url:
                    provider_data = call.get("PROVIDER_DATA") or ""
                    if provider_data and provider_data in voxi_url_map:
                        record_url = voxi_url_map[provider_data]

                if not record_url:
                    skipped_no_file += 1
                    continue

                owner_id = call.get("OWNER_ID")
                owner_type = str(call.get("OWNER_TYPE_ID", ""))
                client_info = await self._get_owner_info(owner_type, owner_id)
                responsible_id = call.get("RESPONSIBLE_ID")
                manager_info = await self._get_user_info(responsible_id)

                start_str = call.get("START_TIME", "")
                start_time = self._parse_bitrix_dt(start_str)
                end_str = call.get("END_TIME", "")
                end_time = self._parse_bitrix_dt(end_str) if end_str else start_time
                duration = max(0, int((end_time - start_time).total_seconds()))

                provider_data = call.get("PROVIDER_DATA") or ""
                if provider_data and provider_data in voxi_duration_map:
                    duration = voxi_duration_map[provider_data]
                elif duration <= 1:
                    phone = client_info.get("phone", "")
                    key = f"{phone}_{start_str[:16]}" if phone and start_str else ""
                    if key and key in voxi_duration_map:
                        duration = voxi_duration_map[key]

                if duration < self.MIN_CALL_DURATION:
                    skipped_short += 1
                    continue

                recordings.append({
                    "crm_record_id": call_id,
                    "crm_call_id": call_id,
                    "call_date": start_time,
                    "duration_seconds": duration,
                    "direction": "inbound" if str(call.get("DIRECTION")) == "1" else "outbound",
                    "recording_url": record_url,
                    "manager_name": manager_info.get("name", ""),
                    "client_name": client_info.get("name", ""),
                    "client_phone": client_info.get("phone", ""),
                    "client_company": client_info.get("company", ""),
                    "crm_metadata": {
                        "subject": call.get("SUBJECT", ""),
                        "owner_type": owner_type,
                        "provider_id": call.get("PROVIDER_ID", ""),
                    },
                })
            except Exception as e:
                logger.warning(f"[Bitrix24] Error processing activity {call.get('ID')}: {e}")

        logger.info(f"[Bitrix24] Activities: {len(all_calls)} total, "
                     f"{skipped_missed} missed, {skipped_short} short(<{self.MIN_CALL_DURATION}s), "
                     f"{skipped_no_file} no file, {len(recordings)} with recording")

        # --- Шаг 2: если из activity ничего нет — voximplant напрямую ---
        if len(recordings) == 0:
            logger.info("[Bitrix24] No recordings from activities, using voximplant fallback...")
            for vid, rec_url in voxi_url_map.items():
                if vid in seen_ids or "_" in vid:
                    continue
                seen_ids.add(vid)
                dur = voxi_duration_map.get(vid, 0)
                if dur < self.MIN_CALL_DURATION:
                    continue
                recordings.append({
                    "crm_record_id": vid,
                    "crm_call_id": vid,
                    "call_date": datetime.utcnow(),
                    "duration_seconds": dur,
                    "direction": "inbound",
                    "recording_url": rec_url,
                    "manager_name": "",
                    "client_name": "",
                    "client_phone": "",
                    "client_company": "",
                    "crm_metadata": {"source": "voximplant"},
                })

        logger.info(f"[Bitrix24] Total recordings collected: {len(recordings)}")
        recordings.sort(key=lambda r: r["call_date"], reverse=True)
        return recordings

    async def _extract_recording_url(self, activity: dict, auth_token: str) -> Optional[str]:
        """Извлечь URL записи звонка из активности Bitrix24"""
        # 1) FILES — Bitrix24 отдаёт список [{"id": N, "url": "..."}, ...]
        files = activity.get("FILES")
        if files:
            if isinstance(files, list):
                for f in files:
                    if isinstance(f, dict) and f.get("url"):
                        url = f["url"]
                        if url.endswith("auth=") and auth_token:
                            url += auth_token
                        elif "auth=" in url and url.split("auth=")[-1] == "" and auth_token:
                            url += auth_token
                        logger.debug(f"[Bitrix24] File URL from FILES list: {url[:120]}...")
                        return url
            elif isinstance(files, dict):
                for fid, finfo in files.items():
                    if isinstance(finfo, dict) and finfo.get("url"):
                        url = finfo["url"]
                        if url.endswith("auth=") and auth_token:
                            url += auth_token
                        return url

        # 2) STORAGE_ELEMENT_IDS → disk.file.get
        storage_ids = activity.get("STORAGE_ELEMENT_IDS") or []
        if storage_ids:
            for sid in storage_ids[:2]:
                try:
                    file_data = await self._make_api_request(
                        "disk.file.get", params={"id": sid}
                    )
                    if file_data and "result" in file_data:
                        dl_url = file_data["result"].get("DOWNLOAD_URL")
                        if dl_url:
                            logger.debug(f"[Bitrix24] File URL from disk.file.get: {dl_url[:120]}...")
                            return dl_url
                except Exception as e:
                    logger.debug(f"[Bitrix24] disk.file.get error for {sid}: {e}")

        return None

    @staticmethod
    def _parse_bitrix_dt(s: str) -> datetime:
        """Парсит datetime из Bitrix24 формата"""
        if not s:
            return datetime.utcnow()
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return datetime.utcnow()

    async def _get_owner_info(self, owner_type: Optional[str], owner_id: Optional[str]) -> Dict[str, Any]:
        """Получить информацию о владельце (контакт/лид/компания)"""
        if not owner_id or not owner_type:
            return {}
        
        method = None
        if owner_type == "1":
            method = "crm.lead.get"
        elif owner_type == "2":
            method = "crm.deal.get"
        elif owner_type == "3":
            method = "crm.contact.get"
        elif owner_type == "4":
            method = "crm.company.get"
        
        if not method:
            return {}
        
        data = await self._make_api_request(method, params={"ID": owner_id})
        if not data or "result" not in data:
            return {}
        
        entity = data["result"]
        info = {
            "name": entity.get("TITLE") or entity.get("NAME", ""),
            "phone": "",
            "company": ""
        }
        
        if "PHONE" in entity and entity["PHONE"]:
            phones = entity["PHONE"]
            if phones and len(phones) > 0:
                info["phone"] = phones[0].get("VALUE", "")
        
        if owner_type == "3" and "COMPANY_ID" in entity:
            company_data = await self._make_api_request(
                "crm.company.get",
                params={"ID": entity["COMPANY_ID"]}
            )
            if company_data and "result" in company_data:
                info["company"] = company_data["result"].get("TITLE", "")
        
        return info
    
    async def _get_user_info(self, user_id: Optional[str]) -> Dict[str, Any]:
        """Получить информацию о пользователе"""
        if not user_id:
            return {}
        
        data = await self._make_api_request("user.get", params={"ID": user_id})
        if not data or "result" not in data or not data["result"]:
            return {}
        
        user = data["result"][0]
        return {
            "name": f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip()
        }
    
    async def get_chats(self, db: Session, limit: int = 100) -> List[Dict[str, Any]]:
        """Получить чаты из Битрикс24: Открытые Линии + групповые/внутренние чаты"""
        chats = []
        
        from datetime import datetime, timedelta
        date_from = (datetime.utcnow() - timedelta(days=730)).isoformat()
        
        # 1) Открытые Линии (чаты с клиентами через каналы)
        activities_data = await self._make_api_request(
            "crm.activity.list",
            params={
                "filter": {
                    "PROVIDER_ID": "IMOPENLINES_SESSION",
                    ">START_TIME": date_from
                },
                "select": [
                    "ID", "OWNER_ID", "OWNER_TYPE_ID", "SUBJECT",
                    "START_TIME", "END_TIME", "DESCRIPTION", "DIRECTION",
                    "RESPONSIBLE_ID", "ASSOCIATED_ENTITY_ID", "SETTINGS"
                ],
                "order": {"START_TIME": "DESC"}
            }
        )
        
        if activities_data and "result" in activities_data and activities_data["result"]:
            activities = activities_data["result"]
            logger.info(f"Found {len(activities)} open line activities")
            
            for activity in activities[:limit]:
                try:
                    activity_id = activity.get("ID")
                    chat_id = activity.get("ASSOCIATED_ENTITY_ID")
                    
                    if not chat_id:
                        settings = activity.get("SETTINGS", {})
                        if isinstance(settings, dict):
                            chat_id = settings.get("CHAT_ID")
                    
                    messages_text = ""
                    if chat_id:
                        messages_text = await self._get_chat_messages(chat_id)
                    
                    if not messages_text:
                        description = activity.get("DESCRIPTION", "")
                        if description:
                            messages_text = description
                        else:
                            continue
                    
                    owner_id = activity.get("OWNER_ID")
                    owner_type = activity.get("OWNER_TYPE_ID")
                    client_info = await self._get_owner_info(owner_type, owner_id)
                    
                    responsible_id = activity.get("RESPONSIBLE_ID")
                    manager_info = await self._get_user_info(responsible_id)
                    
                    start_time = datetime.fromisoformat(activity.get("START_TIME", "").replace("Z", "+00:00"))
                    end_time_str = activity.get("END_TIME", "")
                    if end_time_str:
                        end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                        duration = int((end_time - start_time).total_seconds())
                    else:
                        duration = 0
                    
                    chats.append({
                        "crm_record_id": f"ol_{activity_id}",
                        "crm_call_id": str(chat_id) if chat_id else str(activity_id),
                        "call_date": start_time,
                        "duration_seconds": duration,
                        "direction": "inbound" if activity.get("DIRECTION") == "1" else "outbound",
                        "recording_url": None,
                        "manager_name": manager_info.get("name", ""),
                        "client_name": client_info.get("name", ""),
                        "client_phone": client_info.get("phone", ""),
                        "client_company": client_info.get("company", ""),
                        "record_type": "chat",
                        "chat_text": messages_text,
                        "crm_metadata": {
                            "subject": activity.get("SUBJECT", ""),
                            "source": "openlines",
                            "chat_id": chat_id
                        }
                    })
                except Exception as e:
                    logger.warning(f"Error processing open line activity {activity.get('ID')}: {e}")
                    continue
        else:
            logger.info("No open line activities found")
        
        # 2) Групповые и внутренние чаты (im.recent.list)
        recent_data = await self._make_api_request("im.recent.list", params={})
        
        recent_items = []
        if recent_data and "result" in recent_data:
            result = recent_data["result"]
            if isinstance(result, dict):
                recent_items = result.get("items", [])
            elif isinstance(result, list):
                recent_items = result
        
        group_chats = [item for item in recent_items if item.get("type") == "chat"]
        logger.info(f"Found {len(group_chats)} group/internal chats")
        
        for item in group_chats[:limit - len(chats)]:
            try:
                chat_info = item.get("chat", {})
                dialog_id = item.get("id", "")
                
                if not dialog_id:
                    continue
                
                chat_id_num = dialog_id.replace("chat", "") if dialog_id.startswith("chat") else dialog_id
                
                chat_type = chat_info.get("type", "")
                if chat_type == "generalChannel":
                    continue
                
                messages_text = await self._get_chat_messages(chat_id_num)
                if not messages_text:
                    continue
                
                chat_title = chat_info.get("title") or chat_info.get("name") or f"Чат {dialog_id}"
                date_str = chat_info.get("date_create", "") or item.get("date", "")
                
                try:
                    chat_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")) if date_str else datetime.utcnow()
                except Exception:
                    chat_date = datetime.utcnow()
                
                chats.append({
                    "crm_record_id": f"im_{dialog_id}",
                    "crm_call_id": str(chat_id_num),
                    "call_date": chat_date,
                    "duration_seconds": 0,
                    "direction": "inbound",
                    "recording_url": None,
                    "manager_name": "",
                    "client_name": chat_title,
                    "client_phone": "",
                    "client_company": "",
                    "record_type": "chat",
                    "chat_text": messages_text,
                    "crm_metadata": {
                        "subject": chat_title,
                        "source": "im_chat",
                        "chat_type": chat_type,
                        "dialog_id": dialog_id
                    }
                })
                logger.info(f"Added group chat: {chat_title} ({dialog_id})")
            except Exception as e:
                logger.warning(f"Error processing group chat {item.get('id')}: {e}")
                continue
        
        logger.info(f"Total chats collected: {len(chats)}")
        return chats
    
    async def _get_chat_messages(self, chat_id: str) -> str:
        """Получить сообщения чата по ID и вернуть как текст"""
        try:
            dialog_id = f"chat{chat_id}"
            
            all_messages = []
            last_id = 0
            
            for _ in range(10):
                params = {"DIALOG_ID": dialog_id, "LIMIT": 50}
                if last_id:
                    params["FIRST_ID"] = last_id
                
                data = await self._make_api_request("im.dialog.messages.get", params=params)
                
                if not data or "result" not in data:
                    break
                
                messages = data["result"].get("messages", [])
                if not messages:
                    break
                
                all_messages.extend(messages)
                
                if len(messages) < 50:
                    break
                last_id = messages[-1].get("id", 0)
            
            if not all_messages:
                return ""
            
            users_cache = {}
            lines = []
            
            all_messages.sort(key=lambda m: m.get("date", ""))
            
            for msg in all_messages:
                author_id = msg.get("author_id", 0)
                text = msg.get("text", "").strip()
                date = msg.get("date", "")
                
                if not text:
                    continue
                
                if author_id not in users_cache:
                    user_info = await self._get_user_info(str(author_id))
                    name = user_info.get("name", "")
                    if not name or author_id == 0:
                        name = "Клиент"
                    users_cache[author_id] = name
                
                author_name = users_cache[author_id]
                
                time_str = ""
                if date:
                    try:
                        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
                        time_str = dt.strftime("%H:%M")
                    except Exception:
                        pass
                
                prefix = f"[{time_str}] " if time_str else ""
                lines.append(f"{prefix}{author_name}: {text}")
            
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error getting chat messages for chat {chat_id}: {e}")
            return ""

    async def download_recording(self, recording_url: str, save_path: str) -> bool:
        """Скачать запись звонка"""
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                
                async with client.stream("GET", recording_url) as response:
                    response.raise_for_status()
                    
                    with open(save_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            f.write(chunk)
                
                return True
        except Exception as e:
            logger.error(f"Failed to download Bitrix24 recording: {e}")
            return False


class Bitrix24Service(CRMService):
    """Сервис для интеграции с Bitrix24 через OAuth"""
    
    OAUTH_URL = "https://oauth.bitrix.info/oauth/authorize/"
    
    def __init__(self, integration: CRMIntegration):
        super().__init__(integration)
        self.base_url = f"https://{integration.crm_domain}/rest" if integration.crm_domain else None
    
    def get_oauth_url(self, client_id: str, redirect_uri: str, state: str) -> str:
        """Получить URL для OAuth авторизации"""
        from urllib.parse import urlencode
        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state
        }
        return f"{self.OAUTH_URL}?{urlencode(params)}"
    
    async def exchange_code_for_tokens(self, code: str, redirect_uri: str, client_id: str, client_secret: str, domain: str) -> Dict[str, Any]:
        """Обменять код на токены"""
        url = f"https://oauth.bitrix.info/oauth/token/"
        params = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            # Bitrix24 возвращает данные в немного другом формате
            return {
                "access_token": data["access_token"],
                "refresh_token": data["refresh_token"],
                "expires_in": data.get("expires_in", 3600),
                "domain": data.get("domain", domain)  # Реальный домен портала
            }
    
    async def refresh_access_token(self, db: Session) -> bool:
        """Обновить access token используя refresh token"""
        if not self.refresh_token or not self.integration.client_id:
            return False
        
        url = "https://oauth.bitrix.info/oauth/token/"
        params = {
            "grant_type": "refresh_token",
            "client_id": self._decrypt_token(self.integration.client_id),
            "client_secret": self._decrypt_token(self.integration.client_secret),
            "refresh_token": self.refresh_token
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                token_data = response.json()
                
                self.save_tokens(
                    db,
                    access_token=token_data["access_token"],
                    refresh_token=token_data["refresh_token"],
                    expires_in=token_data.get("expires_in", 3600)
                )
                return True
        except Exception as e:
            logger.error(f"Failed to refresh token for Bitrix24: {e}")
            return False
    
    async def _make_api_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Выполнить запрос к REST API Bitrix24 с автообновлением токена"""
        if not self.access_token or not self.base_url:
            return None
        
        url = f"{self.base_url}/{method}.json"
        
        request_params = dict(params) if params else {}
        request_params["auth"] = self.access_token
        
        has_complex_params = any(isinstance(v, (dict, list)) for k, v in request_params.items() if k != "auth")
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                if has_complex_params:
                    response = await client.post(url, json=request_params)
                else:
                    response = await client.get(url, params=request_params)
                response.raise_for_status()
                data = response.json()
                
                if "error" in data:
                    if data["error"] in ["expired_token", "invalid_token"]:
                        db = SessionLocal()
                        try:
                            if await self.refresh_access_token(db):
                                request_params["auth"] = self.access_token
                                if has_complex_params:
                                    response = await client.post(url, json=request_params)
                                else:
                                    response = await client.get(url, params=request_params)
                                response.raise_for_status()
                                return response.json()
                        finally:
                            db.close()
                    logger.error(f"Bitrix24 API error: {data}")
                    return None
                
                return data
            except Exception as e:
                logger.error(f"Bitrix24 API request failed: {e}")
                return None
    
    async def get_recordings(self, db: Session, limit: int = 100) -> List[Dict[str, Any]]:
        """Получить список записей звонков из Bitrix24 (OAuth)"""
        return await self._bitrix24_get_recordings()

    _bitrix24_get_recordings = Bitrix24WebhookService._bitrix24_get_recordings
    _extract_recording_url = Bitrix24WebhookService._extract_recording_url
    _parse_bitrix_dt = Bitrix24WebhookService._parse_bitrix_dt
    _get_auth_token = Bitrix24WebhookService._get_auth_token

    async def _get_owner_info(self, owner_type: Optional[str], owner_id: Optional[str]) -> Dict[str, Any]:
        """Получить информацию о владельце (контакт/лид/компания)"""
        if not owner_id or not owner_type:
            return {}
        
        method = None
        if owner_type == "1":  # Лид
            method = "crm.lead.get"
        elif owner_type == "2":  # Сделка
            method = "crm.deal.get"
        elif owner_type == "3":  # Контакт
            method = "crm.contact.get"
        elif owner_type == "4":  # Компания
            method = "crm.company.get"
        
        if not method:
            return {}
        
        data = await self._make_api_request(method, params={"ID": owner_id})
        if not data or "result" not in data:
            return {}
        
        entity = data["result"]
        info = {
            "name": entity.get("TITLE") or entity.get("NAME", ""),
            "phone": "",
            "company": ""
        }
        
        # Получаем телефон
        if "PHONE" in entity and entity["PHONE"]:
            phones = entity["PHONE"]
            if phones and len(phones) > 0:
                info["phone"] = phones[0].get("VALUE", "")
        
        # Для контакта получаем компанию
        if owner_type == "3" and "COMPANY_ID" in entity:
            company_data = await self._make_api_request(
                "crm.company.get",
                params={"ID": entity["COMPANY_ID"]}
            )
            if company_data and "result" in company_data:
                info["company"] = company_data["result"].get("TITLE", "")
        
        return info
    
    async def _get_user_info(self, user_id: Optional[str]) -> Dict[str, Any]:
        """Получить информацию о пользователе"""
        if not user_id:
            return {}
        
        data = await self._make_api_request("user.get", params={"ID": user_id})
        if not data or "result" not in data or not data["result"]:
            return {}
        
        user = data["result"][0]
        return {
            "name": f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip()
        }
    
    async def get_chats(self, db: Session, limit: int = 100) -> List[Dict[str, Any]]:
        """Получить чаты из Битрикс24: Открытые Линии + групповые/внутренние чаты (OAuth)"""
        chats = []
        
        from datetime import datetime, timedelta
        date_from = (datetime.utcnow() - timedelta(days=730)).isoformat()
        
        # 1) Открытые Линии
        activities_data = await self._make_api_request(
            "crm.activity.list",
            params={
                "filter": {
                    "PROVIDER_ID": "IMOPENLINES_SESSION",
                    ">START_TIME": date_from
                },
                "select": [
                    "ID", "OWNER_ID", "OWNER_TYPE_ID", "SUBJECT",
                    "START_TIME", "END_TIME", "DESCRIPTION", "DIRECTION",
                    "RESPONSIBLE_ID", "ASSOCIATED_ENTITY_ID", "SETTINGS"
                ],
                "order": {"START_TIME": "DESC"}
            }
        )
        
        if activities_data and "result" in activities_data and activities_data["result"]:
            for activity in activities_data["result"][:limit]:
                try:
                    activity_id = activity.get("ID")
                    chat_id = activity.get("ASSOCIATED_ENTITY_ID")
                    if not chat_id:
                        settings = activity.get("SETTINGS", {})
                        if isinstance(settings, dict):
                            chat_id = settings.get("CHAT_ID")
                    
                    messages_text = ""
                    if chat_id:
                        messages_text = await self._get_chat_messages(chat_id)
                    if not messages_text:
                        messages_text = activity.get("DESCRIPTION", "")
                    if not messages_text:
                        continue
                    
                    owner_id = activity.get("OWNER_ID")
                    owner_type = activity.get("OWNER_TYPE_ID")
                    client_info = await self._get_owner_info(owner_type, owner_id)
                    manager_info = await self._get_user_info(activity.get("RESPONSIBLE_ID"))
                    
                    start_time = datetime.fromisoformat(activity.get("START_TIME", "").replace("Z", "+00:00"))
                    end_time_str = activity.get("END_TIME", "")
                    duration = int((datetime.fromisoformat(end_time_str.replace("Z", "+00:00")) - start_time).total_seconds()) if end_time_str else 0
                    
                    chats.append({
                        "crm_record_id": f"ol_{activity_id}",
                        "crm_call_id": str(chat_id) if chat_id else str(activity_id),
                        "call_date": start_time, "duration_seconds": duration,
                        "direction": "inbound" if activity.get("DIRECTION") == "1" else "outbound",
                        "recording_url": None, "manager_name": manager_info.get("name", ""),
                        "client_name": client_info.get("name", ""), "client_phone": client_info.get("phone", ""),
                        "client_company": client_info.get("company", ""), "record_type": "chat",
                        "chat_text": messages_text,
                        "crm_metadata": {"subject": activity.get("SUBJECT", ""), "source": "openlines", "chat_id": chat_id}
                    })
                except Exception as e:
                    logger.warning(f"Error processing open line activity {activity.get('ID')}: {e}")
        
        # 2) Групповые и внутренние чаты
        recent_data = await self._make_api_request("im.recent.list", params={})
        recent_items = []
        if recent_data and "result" in recent_data:
            result = recent_data["result"]
            recent_items = result.get("items", []) if isinstance(result, dict) else (result if isinstance(result, list) else [])
        
        group_chats = [item for item in recent_items if item.get("type") == "chat"]
        logger.info(f"Found {len(group_chats)} group/internal chats")
        
        for item in group_chats[:limit - len(chats)]:
            try:
                chat_info = item.get("chat", {})
                dialog_id = item.get("id", "")
                if not dialog_id:
                    continue
                
                chat_id_num = dialog_id.replace("chat", "") if dialog_id.startswith("chat") else dialog_id
                if chat_info.get("type") == "generalChannel":
                    continue
                
                messages_text = await self._get_chat_messages(chat_id_num)
                if not messages_text:
                    continue
                
                chat_title = chat_info.get("title") or chat_info.get("name") or f"Чат {dialog_id}"
                date_str = chat_info.get("date_create", "") or item.get("date", "")
                try:
                    chat_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")) if date_str else datetime.utcnow()
                except Exception:
                    chat_date = datetime.utcnow()
                
                chats.append({
                    "crm_record_id": f"im_{dialog_id}", "crm_call_id": str(chat_id_num),
                    "call_date": chat_date, "duration_seconds": 0, "direction": "inbound",
                    "recording_url": None, "manager_name": "", "client_name": chat_title,
                    "client_phone": "", "client_company": "", "record_type": "chat",
                    "chat_text": messages_text,
                    "crm_metadata": {"subject": chat_title, "source": "im_chat", "chat_type": chat_info.get("type", ""), "dialog_id": dialog_id}
                })
                logger.info(f"Added group chat: {chat_title} ({dialog_id})")
            except Exception as e:
                logger.warning(f"Error processing group chat {item.get('id')}: {e}")
        
        logger.info(f"Total chats collected: {len(chats)}")
        return chats
    
    async def _get_chat_messages(self, chat_id: str) -> str:
        """Получить сообщения чата по ID и вернуть как текст"""
        try:
            dialog_id = f"chat{chat_id}"
            
            all_messages = []
            last_id = 0
            
            for _ in range(10):
                params = {"DIALOG_ID": dialog_id, "LIMIT": 50}
                if last_id:
                    params["FIRST_ID"] = last_id
                
                data = await self._make_api_request("im.dialog.messages.get", params=params)
                
                if not data or "result" not in data:
                    break
                
                messages = data["result"].get("messages", [])
                if not messages:
                    break
                
                all_messages.extend(messages)
                
                if len(messages) < 50:
                    break
                last_id = messages[-1].get("id", 0)
            
            if not all_messages:
                return ""
            
            users_cache = {}
            lines = []
            
            all_messages.sort(key=lambda m: m.get("date", ""))
            
            for msg in all_messages:
                author_id = msg.get("author_id", 0)
                text = msg.get("text", "").strip()
                date = msg.get("date", "")
                
                if not text:
                    continue
                
                if author_id not in users_cache:
                    user_info = await self._get_user_info(str(author_id))
                    name = user_info.get("name", "")
                    if not name or author_id == 0:
                        name = "Клиент"
                    users_cache[author_id] = name
                
                author_name = users_cache[author_id]
                
                time_str = ""
                if date:
                    try:
                        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
                        time_str = dt.strftime("%H:%M")
                    except Exception:
                        pass
                
                prefix = f"[{time_str}] " if time_str else ""
                lines.append(f"{prefix}{author_name}: {text}")
            
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error getting chat messages for chat {chat_id}: {e}")
            return ""

    async def download_recording(self, recording_url: str, save_path: str) -> bool:
        """Скачать запись звонка"""
        try:
            headers = {}
            if self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            
            async with httpx.AsyncClient(timeout=300.0) as client:
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                
                async with client.stream("GET", recording_url, headers=headers) as response:
                    response.raise_for_status()
                    
                    with open(save_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            f.write(chunk)
                
                return True
        except Exception as e:
            logger.error(f"Failed to download Bitrix24 recording: {e}")
            return False


class CRMServiceFactory:
    """Фабрика для создания сервисов CRM"""
    
    @staticmethod
    def create(integration: CRMIntegration) -> CRMService:
        """Создать сервис для конкретной CRM"""
        if integration.crm_type == "amocrm":
            return AmoCRMService(integration)
        elif integration.crm_type == "bitrix24":
            # Определяем тип подключения: OAuth или Webhook
            # Если есть refresh_token - это OAuth, иначе - Webhook
            if integration.refresh_token:
                return Bitrix24Service(integration)
            else:
                return Bitrix24WebhookService(integration)
        elif integration.crm_type == "bitrix24_webhook":
            # Явно указан вебхук
            return Bitrix24WebhookService(integration)
        else:
            raise ValueError(f"Unsupported CRM type: {integration.crm_type}")
    
    @staticmethod
    def get_supported_crms() -> List[Dict[str, str]]:
        """Получить список поддерживаемых CRM"""
        return [
            {"type": "amocrm", "name": "AmoCRM", "icon": "🟢"},
            {"type": "bitrix24", "name": "Bitrix24", "icon": "🔵"},
            # {"type": "salesforce", "name": "Salesforce", "icon": "☁️"},
        ]
