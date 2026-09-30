from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def reject_nul(cls, value):
        # PostgreSQL text cannot represent NUL, including identifiers in lists.
        def check(item):
            if isinstance(item, str) and "\x00" in item:
                raise ValueError("文本不能包含空字符。")
            if isinstance(item, (list, tuple)):
                for part in item:
                    check(part)
            elif isinstance(item, dict):
                for key, part in item.items():
                    check(key)
                    check(part)

        check(value)
        return value


class LoginInput(Input):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    password: str = Field(min_length=1, max_length=1024)


class ProfileInput(Input):
    name: Name
    subject: str = Field(max_length=30)
    grade: Literal["高一", "高二", "高三", "跨年级"]


class UserOutput(BaseModel):
    id: str
    username: str
    name: str
    subject: str
    grade: str
    roles: list[str]
    active: bool
    avatarUrl: str | None = None


class PreferencesInput(Input):
    skill: str = Field(default="", max_length=36)
    format: Literal["图片", "PPT", "视频", "音乐", "数字人", "3D"] = "PPT"
    pages: int = Field(default=10, ge=1, le=60)
    style: str = Field(default="简洁教学", max_length=80)


class PasswordInput(Input):
    currentPassword: str = Field(min_length=1, max_length=1024)
    newPassword: str = Field(min_length=6, max_length=1024)


class CreateUserInput(ProfileInput):
    username: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.@-]{3,80}$")]
    password: str = Field(min_length=6, max_length=1024)
    role: Literal["teacher", "admin"] = "teacher"


class UserStateInput(Input):
    active: bool


class ConversationInput(Input):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] = "新对话"


class DraftInput(Input):
    content: str = Field(max_length=200_000)


class ConversationOutput(BaseModel):
    id: str
    title: str
    updatedAt: str


class MessageOutput(BaseModel):
    id: str
    role: str
    content: str
    citations: list
    status: str
    feedback: dict | None = None


class MessageInput(Input):
    content: str = Field(max_length=50_000)
    skillId: str | None = None
    documentIds: list[str] = Field(default_factory=list, max_length=10)
    useKnowledge: bool = True


class SkillInvokeInput(Input):
    content: str = Field(min_length=1, max_length=50_000)
    conversationId: str | None = Field(default=None, max_length=36)
    documentIds: list[str] = Field(default_factory=list, max_length=10)
    useKnowledge: bool = True


class SkillInput(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]
    scope: Literal["private"] = "private"


class SkillOutput(BaseModel):
    id: str
    name: str
    description: str
    prompt: str
    scope: str
    status: str
    version: int
    reviewerNotes: str


class TestCaseInput(Input):
    input: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
    expectedOutput: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]


class SkillReadinessInput(Input):
    riskBoundary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=3000)]
    grades: list[Literal["高一", "高二", "高三"]] = Field(min_length=1, max_length=3)
    tests: list[TestCaseInput] = Field(min_length=5, max_length=50)


class ReviewInput(Input):
    notes: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class RollbackInput(Input):
    version: int = Field(ge=0)


class QuotaAdjustmentInput(Input):
    amount: int = Field(strict=True, ge=-2_000_000_000, le=2_000_000_000)
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class QuotaStateInput(Input):
    frozen: bool
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class QuotaOutput(BaseModel):
    balance: int
    reserved: int
    available: int
    frozen: bool


class QuotaTransactionOutput(BaseModel):
    id: str
    source: str
    kind: Literal["adjustment", "reserve", "settle", "release"]
    balanceDelta: int
    reservedDelta: int
    balanceAfter: int
    reservedAfter: int
    reason: str
    createdAt: str


class AdminQuotaTransactionOutput(QuotaTransactionOutput):
    accountId: str
    teacherName: str
    teacherUsername: str


class AdminQuotaOutput(QuotaOutput):
    id: str
    userId: str
    name: str
    username: str
    active: bool


class PersonalQuotaOutput(QuotaOutput):
    totalAllocated: int
    totalReclaimed: int
    totalConsumed: int


class QuotaSummaryOutput(BaseModel):
    accounts: int
    balance: int
    reserved: int
    available: int
    consumed: int


class ModelInput(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    provider: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,80}$")]
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    apiBaseUrl: str | None = Field(default=None, max_length=500)
    apiKey: SecretStr | None = Field(default=None, repr=False)
    active: bool = False
    contextTokens: int = Field(strict=True, ge=256, le=1_000_000)
    maxOutputTokens: int = Field(strict=True, ge=1, le=100_000)
    timeoutSeconds: int = Field(strict=True, default=90, ge=10, le=300)
    inputPrice: int = Field(strict=True, ge=0, le=100_000_000)
    outputPrice: int = Field(strict=True, ge=0, le=100_000_000)

    @model_validator(mode="after")
    def limits(self):
        if self.maxOutputTokens >= self.contextTokens:
            raise ValueError("Output limit must be less than the context limit")
        if self.provider in ("custom", "deepseek", "doubao", "glm", "claude", "openai") and not self.apiBaseUrl:
            raise ValueError("此模型需要填写 API 地址")
        return self


class ModelUpdateInput(ModelInput):
    expectedRevision: int = Field(ge=1)


class ModelStateInput(Input):
    active: bool
    expectedRevision: int = Field(ge=1)


class ModelOutput(BaseModel):
    id: str
    name: str
    provider: str
    model: str
    apiBaseUrl: str | None
    keyConfigured: bool
    active: bool
    contextTokens: int
    maxOutputTokens: int
    timeoutSeconds: int
    inputPrice: int
    outputPrice: int
    revision: int
    default: bool
    connectionReady: bool


class ProviderOutput(BaseModel):
    id: str
    label: str
    ready: bool


class ModelCheckOutput(BaseModel):
    connected: bool
    inputTokens: int
    outputTokens: int
    latencyMs: int
