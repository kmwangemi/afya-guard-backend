# import logging

# import os
# import secrets
# import uuid
from datetime import timedelta
from typing import Annotated

# import requests
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func

# from jinja2 import Template
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.core.database import get_db
from app.models.user_model import User
from app.schemas.user_schema import TokenResponse

# from src.rate_limiter import limiter
from app.core.security import create_access_token, verify_password

# Constants
# EMAIL_CONFIRMATION_URL = (
#     "https://apex-jobseeker-frontend-app.vercel.app/en/email-confirmation"
# )
# PASSWORD_RESET_URL = (
#     "https://apex-jobseeker-frontend-app.vercel.app/en/reset-password"  # noqa: S105
# )
# MAILGUN_DOMAIN = os.getenv("MAILGUN_DOMAIN")
# MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY")
# MAILGUN_BASE_URL = f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages"

# # Load email template
# with open("src/templates/email_confirmation.html", encoding="utf-8") as file:
#     email_template = Template(file.read())

# # Load password reset email template
# with open("src/templates/password_reset_email.html", encoding="utf-8") as file:
#     password_reset_template = Template(file.read())

auth_router = APIRouter()

DbDependency = Annotated[AsyncSession, Depends(get_db)]
db_dependency = Depends(get_db)
FormData = Annotated[OAuth2PasswordRequestForm, Depends()]


# async def send_verification_email(new_user):
#     """Send verification email with the provided code"""
#     html_content = email_template.render(
#         full_name=(
#             f"{capitalize_first_letter(new_user.first_name)} "
#             f"{capitalize_first_letter(new_user.last_name)}"
#         ),
#         phone=new_user.phone_number,
#         signup_date=format_date(new_user.created_at),
#         email=new_user.email,
#         email_confirmation_link=f"{EMAIL_CONFIRMATION_URL}/{new_user.id}",
#     )
#     body_template = {
#         "from": f"Apex <no-reply@{MAILGUN_DOMAIN}>",
#         "to": new_user.email,
#         "subject": "Email Confirmation",
#         "html": html_content,
#     }
#     if MAILGUN_API_KEY:
#         try:
#             response = requests.post(
#                 MAILGUN_BASE_URL,
#                 auth=("api", MAILGUN_API_KEY),
#                 data=body_template,
#                 timeout=10,
#             )
#             return response.status_code == 200
#         except HTTPException as e:
#             logging.error("Failed to send verification email: %s", str(e))
#             return False
#     return False


# async def send_password_reset_email(first_name, last_name, email, reset_token):
#     """Send password reset email with the provided code"""
#     html_content = password_reset_template.render(
#         full_name=(
#             f"{capitalize_first_letter(first_name)} "
#             f"{capitalize_first_letter(last_name)}"
#         ),
#         reset_token=reset_token,
#         reset_link=f"{PASSWORD_RESET_URL}?token={reset_token}",
#     )
#     body_template = {
#         "from": f"Apex <no-reply@{MAILGUN_DOMAIN}>",
#         "to": email,
#         "subject": "Password Reset - Apex Platform",
#         "html": html_content,
#     }
#     if MAILGUN_API_KEY:
#         try:
#             response = requests.post(
#                 MAILGUN_BASE_URL,
#                 auth=("api", MAILGUN_API_KEY),
#                 data=body_template,
#                 timeout=10,
#             )
#             return response.status_code == 200
#         except HTTPException as e:
#             logging.error("Failed to send password reset email: %s", str(e))
#             return False
#     return False


@auth_router.post("/login", response_model=TokenResponse)
# @limiter.limit("5/hour")
async def login(
    # response: Response,
    form_data: FormData,
    db: DbDependency,
):

    # Lookup user by email (case-insensitive)
    # Note: OAuth2PasswordRequestForm uses "username" field, but we treat it as email
    result = await db.execute(
        select(User).filter(func.lower(User.email) == form_data.username.lower())
    )
    existing_user = result.scalars().first()
    # Verify user exists and password is correct
    # Don't reveal whether email or password was incorrect (security best practice)
    if not existing_user or not verify_password(
        form_data.password, existing_user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Create access token with user ID as subject
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": str(existing_user.id)},
        expires_delta=access_token_expires,
    )
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
    )


# @user_auth_router.post("/logout", response_model=LogoutResponse)
# async def logout(
#     request: Request,
#     response: Response,
#     db: DbDependency,
# ):
#     try:
#         token = request.cookies.get("access_token")
#         if token is None:
#             util.set_error(
#                 "Account is not logged in",
#                 "Unauthorized",
#                 401,
#             )
#             return util.send()

#         response.delete_cookie(
#             key="access_token",
#             httponly=True,
#             secure=True,
#             samesite="none",
#         )
#         return {
#             "message": "Successfully logged out",
#         }
#     except Exception as e:
#         logging.error("Database error occurred: %s", str(e))
#         util.set_error(
#             f"Database error occurred: {str(e)}", "Internal Server Error", 500
#         )
#         return util.send()


# @user_auth_router.post("/registration", response_model=UserResponse)
# # @limiter.limit("5/hour")
# async def create_user(
#     request: Request, register_user_request: JobseekerCreate, db: DbDependency
# ):
#     try:
#         await db.begin()  # ✅ Start transaction manually
#         # Check if email already exists
#         email_result = await db.execute(
#             select(User).filter(User.email == register_user_request.email)
#         )
#         existing_email = email_result.scalars().first()
#         if existing_email:
#             util.set_error("A user with the email already exists.", "Bad Request", 400)
#             return util.send()
#         # Create new user
#         new_user = User(
#             first_name=register_user_request.first_name,
#             last_name=register_user_request.last_name,
#             phone_number=register_user_request.phone_number,
#             email=register_user_request.email,
#             hashed_password=hash_password(register_user_request.password),
#             role="jobseeker",
#         )
#         db.add(new_user)
#         await db.flush()  # ✅ Flush to get new_user.id without committing
#         # ✅ Check role and create associated model
#         new_jobseeker = JobSeeker(user_id=new_user.id)
#         db.add(new_jobseeker)
#         # ✅ Manually commit if everything is successful
#         await db.commit()
#         # ✅ Now send email (since commit was successful)
#         email_sent = await send_verification_email(new_user)
#         if not email_sent:
#             logging.warning("Failed to send verification email to %s", new_user.email)
#         # Exclude fields manually
#         excluded_fields = {"hashed_password"}
#         user_data = {
#             key: value
#             for key, value in new_user.__dict__.items()
#             if key not in excluded_fields and not key.startswith("_")
#         }
#         util.set_success("User created successfully", "Created", 201, user_data)
#         return util.send()
#     except ValueError as e:
#         await db.rollback()  # ✅ Rollback if anything fails
#         logging.error("ValueError occurred: %s", str(e))
#         util.set_error(f"Invalid input: {str(e)}", "Bad Request", 400)
#         return util.send()
#     except SQLAlchemyError as e:
#         await db.rollback()  # ✅ Rollback database transaction
#         logging.error("Database error occurred: %s", str(e))
#         util.set_error(f"Database error: {str(e)}", "Internal Server Error", 500)
#         return util.send()


# @user_auth_router.post("/forgot-password", response_model=UserResponse)
# @limiter.limit("5/hour")
# async def send_reset_password_link(
#     request: Request, reset_password_request: PasswordResetRequest, db: DbDependency
# ):
#     try:
#         result = await db.execute(
#             select(User).filter(User.email == reset_password_request.email)
#         )
#         existing_user = result.scalars().first()
#         if not existing_user:
#             # Return success even if user doesn't exist for security reasons
#             # This prevents email enumeration attacks
#             util.set_success(
#                 (
#                     "If your email exists in our system, "
#                     "a password reset code has been sent."
#                 ),
#                 "Success",
#                 200,
#             )
#             return util.send()
#         # Check if user is a jobseeker
#         if not existing_user.role == "jobseeker":
#             util.set_error(
#                 "You are not authorized to access this resource.",
#                 "Unauthorized",
#                 401,
#             )
#             return util.send()
#         reset_token = secrets.token_urlsafe(32)
#         reset_expiry = datetime.now() + timedelta(hours=1)  # 1 hour expiry
#         existing_user.reset_token = reset_token
#         existing_user.reset_token_expiry = reset_expiry
#         await db.commit()
#         # Send password reset email
#         email_sent = await send_password_reset_email(
#             existing_user.first_name,
#             existing_user.last_name,
#             existing_user.email,
#             reset_token,
#         )
#         if not email_sent:
#             logging.warning(
#                 "Failed to send password reset email to %s",
#                 existing_user.email,
#             )
#         # Exclude fields manually
#         excluded_fields = {"hashed_password"}
#         user_data = {
#             key: value
#             for key, value in existing_user.__dict__.items()
#             if key not in excluded_fields and not key.startswith("_")
#         }
#         util.set_success("Password reset link sent successfully", "OK", 200, user_data)
#         return util.send()
#     except SQLAlchemyError as e:
#         await db.rollback()
#         logging.error("Database error occurred: %s", str(e))
#         util.set_error(f"Database error: {str(e)}", "Internal Server Error", 500)
#         return util.send()


# @user_auth_router.post("/reset-password", response_model=UserResponse)
# @limiter.limit("5/hour")
# async def reset_password(
#     request: Request, reset_password_request: ResetPasswordToken, db: DbDependency
# ):
#     try:
#         result = await db.execute(
#             select(User).filter(User.reset_token == reset_password_request.token)
#         )
#         existing_user = result.scalars().first()
#         if not existing_user:
#             util.set_error("Invalid or expired token", "Unauthorized", 401)
#             return util.send()
#         # Check if user is a jobseeker
#         if not existing_user.role == "jobseeker":
#             util.set_error(
#                 "You are not authorized to access this resource.",
#                 "Unauthorized",
#                 401,
#             )
#             return util.send()
#         # Check if reset code is valid
#         if (
#             not existing_user.reset_token
#             or existing_user.reset_token != reset_password_request.token
#         ):
#             util.set_error("Invalid reset code.", "Bad Request", 400)
#             return util.send()
#         # Check if reset token has expired
#         current_time = datetime.now(timezone.utc)
#         if (
#             not existing_user.reset_token_expiry
#             or current_time > existing_user.reset_token_expiry
#         ):
#             util.set_error(
#                 "Reset token has expired. Please request a new one.", "Bad Request", 400
#             )
#             return util.send()
#         existing_user.hashed_password = hash_password(reset_password_request.password)
#         # Clear verification token after successful password reset
#         existing_user.reset_token = None
#         existing_user.reset_token_expiry = None
#         await db.commit()
#         # Exclude fields manually
#         excluded_fields = {"hashed_password"}
#         user_data = {
#             key: value
#             for key, value in existing_user.__dict__.items()
#             if key not in excluded_fields and not key.startswith("_")
#         }
#         util.set_success("Password reset successfully", "OK", 200, user_data)
#         return util.send()
#     except SQLAlchemyError as e:
#         await db.rollback()
#         logging.error("Database error occurred: %s", str(e))
#         util.set_error(f"Database error: {str(e)}", "Internal Server Error", 500)
#         return util.send()


# @user_auth_router.patch(
#     "/verify-email/{user_id}",
#     response_model=UserResponse,
# )
# async def confirm_user_email(
#     user_id: uuid.UUID,
#     db: AsyncSession = db_dependency,
# ):
#     try:
#         result = await db.execute(select(User).filter(User.id == user_id))
#         existing_user = result.scalars().first()
#         if not existing_user:
#             util.set_error("User not found", "Not Found", 404)
#             return util.send()
#         # Check if user is a jobseeker
#         if not existing_user.role == "jobseeker":
#             util.set_error(
#                 "You are not authorized to access this resource.",
#                 "Unauthorized",
#                 401,
#             )
#             return util.send()
#         existing_user.is_email_verified = True
#         existing_user.is_active = True
#         await db.commit()
#         excluded_fields = {"hashed_password"}
#         user_data = {
#             key: value
#             for key, value in existing_user.__dict__.items()
#             if key not in excluded_fields and not key.startswith("_")
#         }
#         util.set_success("Email verified successfully", "OK", 200, user_data)
#         return util.send()
#     except SQLAlchemyError as e:
#         await db.rollback()
#         logging.error("Email confirmation error occurred: %s", str(e))
#         util.set_error(f"Database error: {str(e)}", "Internal Server Error", 500)
#         return util.send()


# @user_auth_router.patch("/change-password", response_model=UserResponse)
# async def change_password(
#     current_user: CurrentJobseeker,
#     change_password_request: ChangePasswordRequest,
#     db: DbDependency,
# ):
#     user_id = current_user.id
#     try:
#         result = await db.execute(select(User).filter(User.id == user_id))
#         existing_user = result.scalars().first()
#         if not existing_user:
#             util.set_error("User not found", "Unauthorized", 401)
#             return util.send()
#         if not verify_password(
#             change_password_request.old_password, existing_user.hashed_password
#         ):
#             util.set_error("Password is incorrect", "Conflict", 409)
#             return util.send()
#         # Check if user is a jobseeker
#         if existing_user.role != "jobseeker":
#             util.set_error(
#                 "You are not authorized to access this resource.",
#                 "Unauthorized",
#                 401,
#             )
#             return util.send()

#         existing_user.hashed_password = hash_password(change_password_request.password)

#         await db.commit()
#         # Exclude fields manually
#         excluded_fields = {"hashed_password"}
#         user_data = {
#             key: value
#             for key, value in existing_user.__dict__.items()
#             if key not in excluded_fields and not key.startswith("_")
#         }
#         util.set_success("Password changed successfully", "OK", 200, user_data)
#         return util.send()
#     except SQLAlchemyError as e:
#         await db.rollback()
#         logging.error("Database error occurred: %s", str(e))
#         util.set_error(f"Database error: {str(e)}", "Internal Server Error", 500)
#         return util.send()
