import traceback
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, UploadFile
from fastapi.responses import JSONResponse
from config import MODEL, OPENAI_API_KEY, OPENAI_BASE_URL
from routes.actions import update_user, update_useful_message, EMLYResponse, save_config_json_file, get_config_json_file
import logging

from routes import emly_messages, emly_users
from utils.dependencies import invalidate_agent_service
from services.auth.dependencies import get_admin as get_admin_user
from utils.utils import ImportData, ImportStatus, handle_general_exception, safe_filename
from models.emly_messages import EMLYMessageModel, EMLYMessages
from models.emly_users import EMLYUserUpdateForm

from utils.agents import OpenAIAgent
from utils.schemas import FileDeleteRequest


log = logging.getLogger(__name__)

router = APIRouter()





@router.get("/")
async def get_status():
    """
    Retrieve the status of the data import process.

    - If the import process is completed, returns a JSON object with the status set to True,
      authentication details, default models, and default prompt suggestions.
    - If the import process is not started and no import is in progress, starts the import process
      in a separate thread and returns a 400 HTTP error with the detail "Import started."
    - If the import process has failed, returns a 200 HTTP error with the detail "Import data failed."
    - If the import process is still in progress, returns a 400 HTTP error with the detail
      "Import is in progress."

    Returns:
        JSON response with:
        - status: Boolean indicating the success of the import process.
        - auth: Authentication details.
        - default_models: Default models configuration.
        - default_prompt_suggestions: Default prompt suggestions configuration.

    Raises:
        HTTPException:
            - 202: If the import process is still in progress or has started, with a relevant detail message.
            - 200: If the import process has failed, with a detail message.
    """

    return {
        "status": True,
        "auth": "",
        "default_models": "",
        "default_prompt_suggestions": "",
    }



@router.put("/{user_id}", response_model=EMLYResponse)
async def user_update(
        form_data: EMLYUserUpdateForm,
        user_id: str,
        user = Depends(get_admin_user),
        customer_email: str = Query(None),
        customer_callback: str = Query(None)
):
    return await update_user(form_data, user_id, customer_email, customer_callback)

@router.put("/{message_id}")
async def update_useful_emly_message(message_id: str, bot_id: str = Query(...), not_useful: bool = Query(False), user = Depends(get_admin_user)):
    return await update_useful_message(bot_id, message_id, not_useful=not_useful)

@router.get("/emly_users")
async def get_all_emly_users(bot_id: str = Query(...), skip: int = 0, limit: int = 50, user= Depends(get_admin_user)):
    return await emly_users.get_emly_users(bot_id, skip, limit)

@router.get("/emly_user/{user_id}", response_model=EMLYResponse)
async def get_emly_user(user_id: str, bot_id: str = Query(...), user = Depends(get_admin_user)):
    return await emly_users.get_emly_user_by_id(bot_id, user_id)

@router.get("/emly_messages/csv")
async def get_messages_as_csv(
    bot_id: str = Query(...),
    user_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    from_timestamp: Optional[int] = Query(None),
    to_timestamp: Optional[int] = Query(None),
    after_message_id: Optional[int] = Query(None),
    user=Depends(get_admin_user),
):
    try:
        From = datetime.fromtimestamp(from_timestamp) if from_timestamp else None
        To = datetime.fromtimestamp(to_timestamp) if to_timestamp else None
        return await emly_messages.get_messages_as_csv(bot_id, user_id, session_id, From, To, after_message_id)
    except Exception as e:
        print("Error in get_messages_as_csv:", e)
        traceback.print_exc()
        return {"error": "Internal server error", "details": str(e)}

@router.delete("/{message_id}", response_model=bool)
async def delete_message_by_id(message_id: int, bot_id: str = Query(...), user = Depends(get_admin_user)):
    return await emly_messages.delete_message_by_id(bot_id, message_id)

@router.get("/{message_id}", response_model=EMLYMessageModel)
async def get_emly_message_by_id(message_id: str, bot_id: str = Query(...), user = Depends(get_admin_user)):
    return await emly_messages.get_emly_message_by_id(bot_id, message_id)

@router.get("/", response_model=List[EMLYMessageModel])
async def get_emly_messages(bot_id: str = Query(...), skip: int = 0, limit: int = 50, user = Depends(get_admin_user)):
    return await emly_messages.get_messages(bot_id, None, None, skip, limit)

@router.get("/config/json")
def get_config(bot_id: str = Query(...), user = Depends(get_admin_user)):
    return get_config_json_file(bot_id)

@router.put("/config/json")
def update_config(payload: dict, bot_id: str = Query(...), user = Depends(get_admin_user)):
    log.info(f"Configuration to be saved: {payload}\n\n")
    updated_config = save_config_json_file(payload, bot_id)
    invalidate_agent_service(bot_id)
    return updated_config

import_data_manager = ImportData()


@router.get("/metric/report")
async def get_report(
    bot_id: str = Query(...),
    from_timestamp: Optional[int] = Query(None),
    to_timestamp: Optional[int] = Query(None),
    user=Depends(get_admin_user),
):
    try:
        From = datetime.fromtimestamp(from_timestamp) if from_timestamp else None
        To = datetime.fromtimestamp(to_timestamp) if to_timestamp else None
        return EMLYMessages.get_report(bot_id, From, To)
    except Exception as e:
        traceback.print_exception(e)
        return JSONResponse(status_code=500, content={"message": f"Exception occurred: {e}"})

@router.post("/summary/generate")
async def generate_summary(payload: dict, bot_id: str = Query(...), user = Depends(get_admin_user)):
    try:
        if not payload:
            return JSONResponse(status_code=400, content={"message": "Payload is required"})
        payload = payload.get("payload")
        from_timestamp = payload.get("from_timestamp")
        to_timestamp = payload.get("to_timestamp")
        prompt = payload.get("prompt")
        From = datetime.fromtimestamp(from_timestamp) if from_timestamp else None
        To = datetime.fromtimestamp(to_timestamp) if to_timestamp else None
        messages = EMLYMessages.get_messages_from_to(bot_id, From, To)
        report = EMLYMessages.get_report(bot_id, From, To)

        summarizer = OpenAIAgent(
            api_key=OPENAI_API_KEY,
            model=MODEL,
            endpoint=OPENAI_BASE_URL,
            last_n_message=messages,
            prompt_template=prompt,
        )
        prompt = summarizer.generate_summary_prompt(report)
        summary = summarizer.summarize_and_recommend(prompt)

        return JSONResponse(status_code=200, content={"message": "success", "summary": summary})
    except Exception as e:
        traceback.print_exception(e)
        return JSONResponse(status_code=500, content={"message": f"Exception occurred: {e}"})
    
@router.post("/import/data/files")
@handle_general_exception
async def import_data_files(
    files: List[UploadFile],
    background_tasks: BackgroundTasks,
    bot_id: str = Query(...),
    user=Depends(get_admin_user),
):
    import os
    import tempfile
    for file in files:
        if file.filename.split(".")[-1] not in ["csv", "json", "txt", "pdf", "docx", "html", "htm", "md"]:
            return JSONResponse(
                content={
                    "message": f"File type {file.filename.split('.')[-1]} not supported. Supported file types are csv, json, txt, pdf, docx, html, htm, md."},
                status_code=400,
            )
    for job in import_data_manager.requests.values():
        if job['status'] == ImportStatus.RUNNING:
            logging.warning("An import job is already running.")
            return {
                "status": "CONFLICT",
                "message": "A data import  job is already in progress. Wait for it to complete.",
            }
    request_id = await import_data_manager.create_request_id(bot_id)

    temp_folder = os.path.join(tempfile.gettempdir(), bot_id)
    os.makedirs(temp_folder, exist_ok=True)
    saved_file_paths = []

    for file in files:
        file_path = os.path.join(temp_folder, safe_filename(file.filename))
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
        saved_file_paths.append(file_path)

    background_tasks.add_task(
        import_data_manager.import_files_data, request_id, bot_id, saved_file_paths
    )

    return JSONResponse(
        content={
            "status": "RUNNING",
            "progress": 0,
            "error": None,
            "total_documents": len(files),
            "completed_documents": 0,
            "new_documents": 0,
            "deleted_documents": 0,
            "message": "Import started",
        },
        status_code=200,
    )

@router.delete("/import/data/files")
@handle_general_exception
async def delete_import_data_files(
    request: FileDeleteRequest,
    bot_id: str = Query(...),
    user=Depends(get_admin_user),
):
    import os
    import tempfile
    if not request.files:
        return JSONResponse(
            content={"message": "No files provided for deletion."},
            status_code=400,
        )

    temp_folder = os.path.join(tempfile.gettempdir(), bot_id)
    files = [os.path.join(temp_folder, f.file_name) for f in request.files]
    await import_data_manager.delete_files_data(bot_id, files)

    return JSONResponse(
        content={"message": "Files deleted successfully."},
        status_code=200,
    )
