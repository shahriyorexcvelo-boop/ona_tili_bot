from aiogram.fsm.state import State, StatesGroup

class RegistrationState(StatesGroup):
    fullname = State()
    phone = State()

class SolveTestState(StatesGroup):
    test_code = State()

class UploadPostPdfState(StatesGroup):
    pdf_file = State()

class AddAdminState(StatesGroup):
    tg_id_or_user = State()

class SetTimeLimitState(StatesGroup):
    test_id = State()
    time_limit = State()

class BroadcastState(StatesGroup):
    waiting_for_message = State()
    confirm_send = State()

class ScheduleState(StatesGroup):
    waiting_date = State()
    waiting_start = State()
    waiting_end = State()

class SetYoutubeState(StatesGroup):
    waiting_for_url = State()

class StudentTahlilState(StatesGroup):
    waiting_for_code = State()

class EditProfileState(StatesGroup):
    waiting_new_name = State()
    waiting_new_phone = State()

class ContactUserState(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_message = State()

class AdminPdfWatermarkState(StatesGroup):
    waiting_for_pdf = State()
