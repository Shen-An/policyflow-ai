"""Built-in enterprise policy corpus for retrieval evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from backend.app.core.config import Settings, get_settings
from backend.app.db.models import EvalCase, RetrievalEvalItem, User
from backend.app.rag.protocols import DocumentIndexer
from backend.app.schemas.eval import EnterpriseEvalSeedResult
from backend.app.services.eval_dataset_import import (
    _upsert_document,
    ensure_dedicated_eval_knowledge_base,
)

ENTERPRISE_EVAL_KB_CODE = "enterprise_eval_test"
ENTERPRISE_EVAL_KB_NAME = "企业政策测试库"
ENTERPRISE_EVAL_KB_DESCRIPTION = (
    "企业内部政策检索评测专用库，包含人工设计的制度文档、边界问题和多文档问题"
)
ENTERPRISE_EVAL_SUITE = "enterprise_policy_v1"


@dataclass(frozen=True)
class PolicyDocument:
    external_id: str
    title: str
    content: str


@dataclass(frozen=True)
class PolicyCase:
    key: str
    question: str
    answer_keywords: tuple[str, ...]
    relevant_documents: tuple[str, ...]
    difficulty: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class PolicyFact:
    key: str
    question: str
    answer_keywords: tuple[str, ...]
    difficulty: str
    tags: tuple[str, ...]


POLICY_DOCUMENTS = (
    PolicyDocument(
        "policy.leave.annual",
        "员工年假管理办法",
        """员工年假管理办法（2026版）

1. 入职满1年不满10年的员工，每年享有5个工作日带薪年假；满10年不满20年的，每年10个工作日；满20年的，每年15个工作日。
2. 年假原则上应在当年度使用。因业务安排确实无法休完的，经直属主管和人力资源部确认后，可顺延至次年3月31日前使用，逾期自动失效。
3. 年假申请应至少提前3个工作日在企业协同平台提交；连续休假超过5个工作日的，还需部门负责人审批。
4. 试用期员工可以提交年假申请，但可休天数按实际累计天数折算，不得预支未形成的年假。
5. 法定节假日、周末和公司统一休假日不计入年假天数。""",
    ),
    PolicyDocument(
        "policy.leave.sick",
        "员工病假与医疗证明规范",
        """员工病假与医疗证明规范（2026版）

1. 病假须在当日上班前通过协同平台或电话向直属主管报备；因突发急诊无法提前报备的，应在就医后4小时内补报。
2. 连续病假超过2个工作日，须提交二级及以上公立医院或公司认可医疗机构出具的诊断证明和建议休息期限。
3. 病假工资、医疗期和长期病假管理按照劳动合同、员工手册及适用法律执行，本规范只规定报备和证明材料要求。
4. 虚假病假证明或无故不报备，按员工纪律管理办法处理。""",
    ),
    PolicyDocument(
        "policy.expense.travel",
        "差旅与住宿报销标准",
        """差旅与住宿报销标准（2026版）

1. 国内出差交通工具原则上选择高铁二等座、动车二等座或经济舱；经理级及以上因工作需要可申请高铁一等座或公务舱，须在申请中说明理由。
2. 住宿标准按城市分级：一线城市员工每晚不超过600元，其他城市每晚不超过400元。超过标准须在出差前取得部门负责人书面批准。
3. 出差申请应在出发前提交，紧急出差可在出发后1个工作日内补提；未提交出差申请的费用原则上不予报销。
4. 报销须附发票、行程单或交通凭证、出差申请单和住宿清单。""",
    ),
    PolicyDocument(
        "policy.expense.meal",
        "商务招待与出差餐费规范",
        """商务招待与出差餐费规范（2026版）

1. 国内出差餐费补贴标准为每人每天早餐30元、午餐60元、晚餐60元；由公司或客户提供的餐食应从对应补贴中扣除。
2. 商务招待须事前填写招待申请，列明客户、参加人员、事由和预计金额；单次预计金额超过2000元须由部门负责人和财务负责人共同审批。
3. 招待费用不得用于个人消费、烟酒礼品或与业务无关的家庭成员消费。
4. 餐费和招待费应在发生后30日内提交报销，逾期需说明原因并经财务负责人批准。""",
    ),
    PolicyDocument(
        "policy.procurement.approval",
        "采购与合同审批矩阵",
        """采购与合同审批矩阵（2026版）

1. 单笔采购金额不超过5000元，由申请人、直属主管审批后执行；5000元以上至50000元，增加部门负责人审批。
2. 单笔采购金额超过50000元，除部门负责人审批外，还须财务负责人和分管副总裁审批；超过200000元须提交采购委员会审议。
3. 任何金额的关联交易、单一来源采购或合同期限超过2年的采购，均须法务审核并在申请中披露原因。
4. 未经审批不得拆分订单规避审批门槛。采购订单、合同和验收记录应归档保存。""",
    ),
    PolicyDocument(
        "policy.remote.work",
        "远程办公与办公地点管理",
        """远程办公与办公地点管理办法（2026版）

1. 员工每月最多申请4个工作日远程办公，须至少提前1个工作日提交申请并获得直属主管批准。
2. 处理客户生产事故、值班、现场交付和涉及受限数据的工作时，不得仅以远程办公方式完成，除非信息安全负责人另行批准。
3. 远程办公期间须使用公司管理的设备、企业VPN和公司协同账号；禁止在公共电脑或公共Wi-Fi上传输内部文件。
4. 远程办公不改变考勤、信息安全和保密义务。""".replace("员工", "员工"),
    ),
    PolicyDocument(
        "policy.security.classification",
        "信息分类与外发控制",
        """信息分类与外发控制规范（2026版）

1. 公司信息分为公开、内部、机密和受限四级。未标注的信息默认按内部信息处理。
2. 内部信息仅限有业务需要的员工访问；机密信息外发前须由信息所有者和部门负责人审批，并使用公司批准的加密渠道。
3. 受限信息包括个人敏感信息、客户密钥、生产凭据和未公开财务数据。受限信息原则上不得通过个人邮箱、公共网盘或即时通讯工具外发。
4. 任何疑似误发、泄露或异常下载，应在发现后1小时内通过安全事件通道报告。""",
    ),
    PolicyDocument(
        "policy.security.incident",
        "安全事件响应流程",
        """安全事件响应流程（2026版）

1. 员工发现账号被盗、恶意软件、数据误发、密钥泄露或异常访问时，应立即停止扩大影响的操作，并在1小时内向信息安全团队报案。
2. 报告内容至少包括发现时间、涉及系统、疑似数据范围、已采取的隔离措施和联系人，不要求员工自行判断事件等级。
3. 信息安全团队负责分级、取证、遏制、恢复和复盘；业务部门须配合保留日志，不得擅自删除证据。
4. 生产系统凭据疑似泄露时，应优先吊销或轮换凭据，再按照事件流程补充报告。""".replace("员工", "员工"),
    ),
    PolicyDocument(
        "policy.access.lifecycle",
        "账号权限申请与离职回收",
        """账号权限申请与离职回收规范（2026版）

1. 新增权限须由直属主管发起申请，并说明业务目的、资源范围和有效期限；高权限还须系统负责人审批。
2. 临时权限最长有效期为30天，到期自动回收；确需延长必须重新提交申请，不得通过共享账号延长权限。
3. 员工离职或劳动关系终止时，人力资源部应在确认离职时间后通知IT；IT须在员工最后工作日结束前禁用账号并回收公司设备。
4. 部门负责人每季度复核本部门权限，发现不再需要的权限应在5个工作日内申请回收。""".replace("员工", "员工"),
    ),
    PolicyDocument(
        "policy.compliance.conflict",
        "利益冲突与礼品接待规则",
        """利益冲突与礼品接待规则（2026版）

1. 员工与供应商、客户或竞争对手存在亲属、投资、兼职或其他可能影响公正履职的关系，应在参与相关决策前主动申报。
2. 不得收受可能影响采购、招聘、合同或验收决定的现金、购物卡和有价证券。
3. 单项礼品价值不超过300元且不影响独立判断的，可以接受；超过300元或无法判断是否影响公正的，应在5个工作日内登记并交合规部门处理。
4. 业务招待应坚持必要、合理、透明原则，不得安排明显超出业务需要的娱乐活动。""".replace("员工", "员工"),
    ),
    PolicyDocument(
        "policy.records.retention",
        "业务记录与数据保留期限",
        """业务记录与数据保留期限（2026版）

1. 已审批的采购合同、验收记录和发票至少保存8年；未成交的采购比价与审批记录至少保存3年。
2. 员工考勤、薪资和劳动合同资料按照人力资源档案规则保存，离职后至少保存5年；法律法规要求更长时从其规定。
3. 安全事件日志、调查材料和复盘报告至少保存2年；涉及诉讼、审计或监管调查的资料，在事项结束前不得删除。
4. 任何删除或匿名化操作都必须经过数据所有者确认，并保留操作记录。""".replace("员工", "员工"),
    ),
    PolicyDocument(
        "policy.vendor.onboarding",
        "供应商准入与年度复核",
        """供应商准入与年度复核规范（2026版）

1. 新供应商准入前须完成主体资质、受益所有人、制裁名单和利益冲突检查，并取得业务负责人和采购部门确认。
2. 供应商需要接触公司内部或客户数据时，须在合同中加入保密、数据处理、分包限制和安全事件通知条款，并由法务和信息安全审核。
3. 关键供应商每年复核一次，复核内容包括服务质量、财务风险、合规事件和权限使用情况。
4. 供应商退出时，业务负责人须确认资料返还或删除，IT须回收账号和接口凭据。""",
    ),
)


_CORE_POLICY_CASES = (
    PolicyCase("leave-days", "入职满12年员工每年有多少天带薪年假？", ("10个工作日",), ("policy.leave.annual",), "direct", ("数字", "HR")),
    PolicyCase("leave-carryover", "今年没有休完的年假最晚可以用到什么时候？", ("次年3月31日", "逾期失效"), ("policy.leave.annual",), "boundary", ("例外", "HR")),
    PolicyCase("leave-approval", "连续申请6个工作日年假需要谁审批？", ("直属主管", "部门负责人"), ("policy.leave.annual",), "multi-step", ("流程", "HR")),
    PolicyCase("sick-proof", "连续病假3个工作日需要准备什么证明？", ("二级及以上公立医院", "诊断证明"), ("policy.leave.sick",), "direct", ("材料", "HR")),
    PolicyCase("travel-hotel", "员工在一线城市出差住宿每晚的标准是多少？", ("600元",), ("policy.expense.travel",), "数字", ("费用", "财务")),
    PolicyCase("travel-over-limit", "住宿超过标准但确有业务需要，应该什么时候取得批准？", ("出差前", "部门负责人"), ("policy.expense.travel",), "boundary", ("例外", "财务")),
    PolicyCase("meal-provided", "出差当天午餐由公司提供，午餐补贴如何处理？", ("扣除", "60元"), ("policy.expense.meal",), "exception", ("费用", "财务")),
    PolicyCase("meal-late", "餐费报销超过30日还能直接提交吗？", ("说明原因", "财务负责人批准"), ("policy.expense.meal",), "boundary", ("时限", "财务")),
    PolicyCase("procurement-80k", "8万元采购需要哪些审批？", ("部门负责人", "财务负责人", "分管副总裁"), ("policy.procurement.approval",), "数字", ("审批", "采购")),
    PolicyCase("procurement-split", "能否把一笔8万元采购拆成多张5000元订单来减少审批？", ("不得拆分", "规避审批"), ("policy.procurement.approval",), "negative", ("边界", "采购")),
    PolicyCase("remote-limit", "员工每月最多可以申请几天远程办公？", ("4个工作日",), ("policy.remote.work",), "direct", ("数字", "办公")),
    PolicyCase("remote-sensitive", "处理受限数据时能否直接远程办公？", ("不得", "信息安全负责人批准"), ("policy.remote.work", "policy.security.classification"), "cross-policy", ("安全", "例外")),
    PolicyCase("classification-default", "没有标注的信息默认按什么级别处理？", ("内部",), ("policy.security.classification",), "direct", ("分类", "安全")),
    PolicyCase("classification-channel", "机密信息外发前需要满足哪些条件？", ("信息所有者", "部门负责人", "加密渠道"), ("policy.security.classification",), "multi-step", ("外发", "安全")),
    PolicyCase("incident-deadline", "发现客户密钥疑似泄露后多久内要报告？", ("1小时", "安全事件通道"), ("policy.security.classification", "policy.security.incident"), "cross-policy", ("事件", "安全")),
    PolicyCase("incident-credential", "生产凭据泄露时最先应该做什么？", ("吊销", "轮换凭据"), ("policy.security.incident",), "ordered", ("事件", "安全")),
    PolicyCase("access-temporary", "临时权限最长有效期是多少？到期后如何继续使用？", ("30天", "重新提交申请"), ("policy.access.lifecycle",), "boundary", ("权限", "IT")),
    PolicyCase("access-offboarding", "员工最后工作日结束前，IT需要完成哪些离职动作？", ("禁用账号", "回收公司设备"), ("policy.access.lifecycle",), "ordered", ("离职", "IT")),
    PolicyCase("vendor-data", "供应商要接触客户数据，合同和审核上至少要补哪些控制？", ("保密", "数据处理", "法务", "信息安全"), ("policy.vendor.onboarding",), "multi-step", ("供应商", "安全")),
    PolicyCase("vendor-exit", "关键供应商退出时，如何确认数据和访问权限已经处理？", ("资料返还或删除", "回收账号", "接口凭据"), ("policy.vendor.onboarding", "policy.access.lifecycle"), "cross-policy", ("供应商", "权限")),
    PolicyCase("gift-threshold", "收到价值500元礼品后应该怎么处理？", ("5个工作日", "登记", "合规部门"), ("policy.compliance.conflict",), "boundary", ("合规", "礼品")),
    PolicyCase("conflict-disclose", "参与供应商采购决策前发现亲属关系，需要什么时候申报？", ("参与相关决策前", "主动申报"), ("policy.compliance.conflict", "policy.procurement.approval"), "cross-policy", ("合规", "采购")),
    PolicyCase("records-contract", "已审批采购合同、验收记录和发票至少保存多久？", ("8年",), ("policy.records.retention",), "direct", ("留存", "采购")),
    PolicyCase("records-incident", "涉及监管调查的安全事件材料可以按普通日志期限删除吗？", ("不得删除", "事项结束前"), ("policy.records.retention", "policy.security.incident"), "negative", ("留存", "安全")),
)


# Each fact has two natural-language query variants so the suite tests retrieval
# against both policy lookups and employee task-oriented questions.
POLICY_FACTS: dict[str, tuple[PolicyFact, ...]] = {
    "policy.leave.annual": (
        PolicyFact("under-ten-years", "入职满12年员工每年有多少天带薪年假", ("10个工作日",), "direct", ("数字", "HR")),
        PolicyFact("ten-to-twenty-years", "工龄满10年但不满20年每年有多少天年假", ("10个工作日",), "boundary", ("边界", "HR")),
        PolicyFact("twenty-years", "工龄满20年的员工每年享有多少天年假", ("15个工作日",), "boundary", ("边界", "HR")),
        PolicyFact("current-year-use", "年假原则上应当在哪个年度内使用", ("当年度使用",), "direct", ("期限", "HR")),
        PolicyFact("carryover-confirmation", "因业务原因休不完年假需要谁确认才能顺延", ("直属主管", "人力资源部", "次年3月31日前"), "multi-step", ("例外", "HR")),
        PolicyFact("request-notice", "年假申请至少要提前几个工作日提交", ("3个工作日",), "direct", ("期限", "HR")),
        PolicyFact("long-leave-approval", "连续休假超过5个工作日还需要增加哪一级审批", ("部门负责人审批",), "boundary", ("审批", "HR")),
        PolicyFact("probation-proration", "试用期员工申请年假时天数应如何计算", ("实际累计天数折算", "不得预支"), "exception", ("例外", "HR")),
    ),
    "policy.leave.sick": (
        PolicyFact("report-before-work", "员工请病假应在什么时候向直属主管报备", ("当日上班前",), "direct", ("流程", "HR")),
        PolicyFact("emergency-report", "突发急诊无法提前报备时最迟多久补报", ("就医后4小时内",), "exception", ("例外", "HR")),
        PolicyFact("certificate-threshold", "连续病假超过2个工作日要提交什么材料", ("二级及以上公立医院", "诊断证明", "建议休息期限"), "boundary", ("材料", "HR")),
        PolicyFact("sick-leave-basis", "病假工资和医疗期依据哪些文件和规则执行", ("劳动合同", "员工手册", "适用法律"), "direct", ("规则", "HR")),
        PolicyFact("fake-certificate", "提交虚假病假证明会按照什么制度处理", ("员工纪律管理办法",), "negative", ("违规", "HR")),
        PolicyFact("missing-report", "无正当理由不报备病假会有什么后果", ("员工纪律管理办法",), "negative", ("违规", "HR")),
    ),
    "policy.expense.travel": (
        PolicyFact("domestic-transport", "国内出差交通工具原则上应选择什么等级", ("高铁二等座", "动车二等座", "经济舱"), "direct", ("标准", "财务")),
        PolicyFact("senior-transport", "经理级及以上因工作需要乘坐高铁一等座需要做什么", ("出差申请", "说明理由"), "exception", ("例外", "财务")),
        PolicyFact("tier-one-hotel", "一线城市员工住宿每晚最高可以报销多少", ("600元",), "direct", ("数字", "财务")),
        PolicyFact("other-city-hotel", "其他城市员工住宿每晚最高可以报销多少", ("400元",), "boundary", ("数字", "财务")),
        PolicyFact("over-limit-approval", "住宿预计超过标准时应在什么时候取得书面批准", ("出差前", "部门负责人书面批准"), "boundary", ("例外", "财务")),
        PolicyFact("urgent-travel", "紧急出差来不及提前申请时可以在什么时候补提", ("出发后1个工作日内",), "exception", ("期限", "财务")),
        PolicyFact("missing-application", "没有提交出差申请的费用原则上能否报销", ("不予报销",), "negative", ("违规", "财务")),
        PolicyFact("travel-receipts", "出差报销至少要附上哪些凭证", ("发票", "行程单", "交通凭证", "出差申请单", "住宿清单"), "multi-step", ("材料", "财务")),
    ),
    "policy.expense.meal": (
        PolicyFact("breakfast-subsidy", "国内出差早餐补贴标准是多少", ("30元",), "direct", ("数字", "财务")),
        PolicyFact("lunch-subsidy", "国内出差午餐补贴标准是多少", ("60元",), "direct", ("数字", "财务")),
        PolicyFact("dinner-subsidy", "国内出差晚餐补贴标准是多少", ("60元",), "direct", ("数字", "财务")),
        PolicyFact("provided-meal", "公司或客户提供餐食时对应餐补怎么处理", ("从对应补贴中扣除",), "exception", ("例外", "财务")),
        PolicyFact("reception-fields", "商务接待申请需要列明哪些信息", ("客户", "参加人员", "事由", "预计金额"), "multi-step", ("流程", "财务")),
        PolicyFact("reception-high-value", "单次预计接待金额超过3000元需要谁共同审批", ("部门负责人", "财务负责人"), "boundary", ("审批", "财务")),
        PolicyFact("prohibited-reception", "接待费用不得用于哪些个人或无关消费", ("个人消费", "烟酒礼品", "家庭成员消费"), "negative", ("违规", "财务")),
        PolicyFact("meal-reimbursement-deadline", "餐费和接待费应在发生后多久提交报销", ("30日内",), "boundary", ("期限", "财务")),
    ),
    "policy.procurement.approval": (
        PolicyFact("under-five-thousand", "单笔不超过5000元的采购由谁审批", ("申请人", "直属主管"), "boundary", ("审批", "采购")),
        PolicyFact("five-to-fifty-thousand", "5000元以上至50000元采购要增加哪一级审批", ("部门负责人"), "boundary", ("审批", "采购")),
        PolicyFact("over-fifty-thousand", "单笔采购超过50000元还需要哪些审批", ("财务负责人", "分管副总裁"), "boundary", ("审批", "采购")),
        PolicyFact("over-two-hundred-thousand", "采购金额超过200000元还要提交什么会议审议", ("采购委员会",), "boundary", ("审批", "采购")),
        PolicyFact("related-transaction", "关联交易采购申请前需要完成什么审查", ("法务审核", "披露原因"), "multi-step", ("合规", "采购")),
        PolicyFact("single-source", "单一来源采购需要什么审查和说明", ("法务审核", "披露原因"), "exception", ("例外", "采购")),
        PolicyFact("long-contract", "合同期限超过2年的采购需要什么额外步骤", ("法务审核", "披露原因"), "boundary", ("期限", "采购")),
        PolicyFact("no-order-splitting", "能否拆分未经审批的采购订单来规避审批", ("不得拆分", "规避审批"), "negative", ("违规", "采购")),
    ),
    "policy.remote.work": (
        PolicyFact("monthly-limit", "员工每月最多可以申请几天远程办公", ("4个工作日",), "direct", ("数字", "办公")),
        PolicyFact("remote-notice", "远程办公申请至少要提前多久提交", ("1个工作日",), "direct", ("期限", "办公")),
        PolicyFact("remote-approval", "远程办公申请需要谁批准", ("直属主管批准",), "direct", ("审批", "办公")),
        PolicyFact("onsite-work", "值班或现场交付工作能否只用远程办公完成", ("不得",), "negative", ("例外", "办公")),
        PolicyFact("sensitive-work", "涉及受限数据时远程办公还需要谁批准", ("信息安全负责人另行批准",), "exception", ("安全", "办公")),
        PolicyFact("managed-equipment", "远程办公期间必须使用哪些公司资源", ("公司管理的设备", "企业VPN", "公司协同账号"), "multi-step", ("安全", "办公")),
        PolicyFact("public-network", "能否在公共电脑或公共Wi-Fi上传输内部文件", ("禁止",), "negative", ("安全", "办公")),
    ),
    "policy.security.classification": (
        PolicyFact("four-levels", "公司信息分为哪四个等级", ("公开", "内部", "机密", "受限"), "direct", ("分类", "安全")),
        PolicyFact("unmarked-default", "没有标注的信息默认按什么等级处理", ("内部信息",), "boundary", ("分类", "安全")),
        PolicyFact("internal-access", "内部信息的访问范围应如何确定", ("业务需要",), "direct", ("权限", "安全")),
        PolicyFact("confidential-external", "机密信息对外发送前需要哪些审批和传输措施", ("信息所有者", "部门负责人", "加密渠道"), "multi-step", ("外发", "安全")),
        PolicyFact("restricted-definition", "哪些内容属于受限信息", ("个人敏感信息", "客户密钥", "生产凭据", "未公开财务数据"), "direct", ("分类", "安全")),
        PolicyFact("restricted-channels", "受限信息原则上不能通过哪些渠道外发", ("个人邮箱", "公共网盘", "即时通讯工具"), "negative", ("外发", "安全")),
        PolicyFact("suspected-disclosure", "发现疑似误发或泄露后应在多久内报告", ("1小时内", "安全事件通道"), "boundary", ("期限", "安全")),
    ),
    "policy.security.incident": (
        PolicyFact("stop-spread", "发现账号被盗或数据误发后第一步应该做什么", ("立即停止扩大影响的操作",), "ordered", ("流程", "安全")),
        PolicyFact("incident-deadline", "安全事件发现后应在多久内向安全团队报告", ("1小时内",), "boundary", ("期限", "安全")),
        PolicyFact("report-time", "安全事件报告必须包含发现时间和哪些范围信息", ("发现时间", "系统", "疑似数据范围"), "multi-step", ("材料", "安全")),
        PolicyFact("no-self-severity", "员工报告安全事件时是否需要自行判断事件等级", ("不要求自行判断事件等级",), "direct", ("规则", "安全")),
        PolicyFact("security-responsibility", "信息安全团队负责安全事件的哪些环节", ("分级", "取证", "遏制", "恢复", "复盘"), "multi-step", ("职责", "安全")),
        PolicyFact("preserve-logs", "业务部门在安全事件处理中必须保留什么", ("日志",), "direct", ("证据", "安全")),
        PolicyFact("rotate-credentials", "生产系统疑似泄露凭据时应先做什么再补充报告", ("吊销或轮换凭据",), "ordered", ("顺序", "安全")),
    ),
    "policy.access.lifecycle": (
        PolicyFact("access-request-fields", "新增账号权限申请需要说明哪些内容", ("业务目的", "资源范围", "有效期限"), "multi-step", ("申请", "IT")),
        PolicyFact("high-privilege", "高权限账号申请还需要谁审核", ("系统负责人审核",), "boundary", ("审批", "IT")),
        PolicyFact("temporary-limit", "临时权限最长可以有效多少天", ("30天",), "direct", ("数字", "IT")),
        PolicyFact("temporary-renewal", "临时权限到期后想继续使用应该怎么做", ("重新提交申请",), "boundary", ("期限", "IT")),
        PolicyFact("offboarding-notify", "员工离职确认后人力资源部需要通知谁", ("IT",), "ordered", ("离职", "IT")),
        PolicyFact("offboarding-disable", "员工最后工作日结束前IT需要完成什么账号动作", ("禁用账号",), "boundary", ("离职", "IT")),
        PolicyFact("quarterly-review", "部门权限复核发现不再需要的权限应在多久内回收", ("5个工作日内",), "boundary", ("复核", "IT")),
    ),
    "policy.compliance.conflict": (
        PolicyFact("disclose-before-decision", "发现与供应商存在亲属关系应在什么时候主动申报", ("参与相关决策前", "主动申报"), "boundary", ("申报", "合规")),
        PolicyFact("conflict-scope", "哪些关系可能影响公正履职并需要申报", ("亲属", "投资", "兼职"), "direct", ("范围", "合规")),
        PolicyFact("prohibited-gifts", "哪些形式的礼品礼金不得接受", ("现金", "购物卡", "有价证券"), "negative", ("礼品", "合规")),
        PolicyFact("small-gift", "价值不超过300元且不影响独立判断的礼品能否接受", ("可以接受",), "boundary", ("数字", "合规")),
        PolicyFact("large-gift", "价值超过300元的礼品应在多久内登记并交合规处理", ("5个工作日内", "登记", "合规部门"), "boundary", ("期限", "合规")),
        PolicyFact("reception-principles", "业务招待安排应遵循哪些原则", ("必要", "合理", "透明"), "direct", ("招待", "合规")),
        PolicyFact("entertainment-limit", "能否安排明显超出业务需要的娱乐活动作为业务招待", ("不得",), "negative", ("招待", "合规")),
    ),
    "policy.records.retention": (
        PolicyFact("approved-procurement", "已审批采购合同验收记录和发票至少保存多少年", ("8年",), "direct", ("期限", "留存")),
        PolicyFact("unfinished-procurement", "未成交采购的比价和审批记录至少保存多少年", ("3年",), "direct", ("期限", "留存")),
        PolicyFact("employee-records", "考勤薪资和劳动合同资料离职后至少保存多久", ("5年",), "boundary", ("期限", "留存")),
        PolicyFact("longer-law", "法律法规要求更长保存期限时应如何处理", ("按法律法规要求",), "exception", ("例外", "留存")),
        PolicyFact("security-logs", "安全事件日志调查材料和复盘报告至少保存多久", ("2年",), "direct", ("期限", "留存")),
        PolicyFact("investigation-hold", "涉及诉讼审计或监管调查的资料在什么时候可以删除", ("事项结束前不得删除",), "negative", ("合规", "留存")),
        PolicyFact("deletion-approval", "删除或匿名化数据前需要谁确认", ("数据所有者确认",), "multi-step", ("审批", "留存")),
    ),
    "policy.vendor.onboarding": (
        PolicyFact("vendor-screening", "新供应商准入前需要完成哪些基础检查", ("主体资质", "受益所有人", "制裁名单", "利益冲突"), "multi-step", ("准入", "供应商")),
        PolicyFact("vendor-confirmation", "供应商准入需要谁确认", ("业务负责人", "采购部门"), "direct", ("审批", "供应商")),
        PolicyFact("vendor-data-clauses", "供应商接触公司或客户数据时合同必须加入哪些条款", ("保密", "数据处理", "分包限制", "安全事件通知"), "multi-step", ("合同", "供应商")),
        PolicyFact("vendor-legal-security", "涉及数据的供应商合同需要经过哪些部门审核", ("法务", "信息安全"), "direct", ("审批", "供应商")),
        PolicyFact("key-vendor-review", "关键供应商多久复核一次", ("每年一次",), "direct", ("期限", "供应商")),
        PolicyFact("review-content", "关键供应商年度复核包括哪些内容", ("服务质量", "财务风险", "合规事件", "权限使用情况"), "multi-step", ("复核", "供应商")),
        PolicyFact("vendor-exit-data", "供应商退出时业务负责人需要确认什么", ("资料返还或删除",), "ordered", ("退出", "供应商")),
        PolicyFact("vendor-exit-access", "供应商退出时IT需要回收哪些访问能力", ("账号", "接口凭据"), "ordered", ("退出", "供应商")),
    ),
}


def _build_policy_cases() -> tuple[PolicyCase, ...]:
    generated: list[PolicyCase] = []
    document_ids = {item.external_id for item in POLICY_DOCUMENTS}
    if set(POLICY_FACTS) != document_ids:
        raise ValueError("POLICY_FACTS must cover every policy document exactly once")

    for document_id, facts in POLICY_FACTS.items():
        for fact in facts:
            for variant, prefix in (
                ("direct", "根据企业制度，"),
                ("scenario", "我需要办理这件事，制度要求是什么："),
            ):
                generated.append(
                    PolicyCase(
                        key=f"fact:{document_id}:{fact.key}:{variant}",
                        question=f"{prefix}{fact.question}",
                        answer_keywords=fact.answer_keywords,
                        relevant_documents=(document_id,),
                        difficulty=fact.difficulty,
                        tags=fact.tags,
                    )
                )

    cases = _CORE_POLICY_CASES + tuple(generated)
    if len(cases) != 200:
        raise ValueError(f"enterprise policy suite must contain 200 cases, got {len(cases)}")
    if len({item.key for item in cases}) != len(cases):
        raise ValueError("enterprise policy suite contains duplicate case keys")
    if len({item.question for item in cases}) != len(cases):
        raise ValueError("enterprise policy suite contains duplicate questions")
    return cases


POLICY_CASES = _build_policy_cases()


def seed_enterprise_eval_dataset(
    session: Session,
    user: User,
    *,
    indexer: DocumentIndexer | None = None,
    settings: Settings | None = None,
) -> EnterpriseEvalSeedResult:
    """Idempotently materialize the built-in enterprise policy evaluation suite."""

    app_settings = settings or get_settings()
    knowledge_base = ensure_dedicated_eval_knowledge_base(
        session,
        settings=app_settings,
        actor=user,
        code=ENTERPRISE_EVAL_KB_CODE,
        name=ENTERPRISE_EVAL_KB_NAME,
        description=ENTERPRISE_EVAL_KB_DESCRIPTION,
    )

    documents_created = 0
    documents_reused = 0
    created_document_ids: list[str] = []
    document_ids: dict[str, str] = {}
    document_titles: dict[str, str] = {}
    for policy in POLICY_DOCUMENTS:
        document, created = _upsert_document(
            session,
            settings=app_settings,
            user=user,
            knowledge_base_id=knowledge_base.id,
            external_id=policy.external_id,
            title=policy.title,
            text=policy.content,
        )
        document_ids[policy.external_id] = document.id
        document_titles[policy.external_id] = policy.title
        if created:
            documents_created += 1
            created_document_ids.append(document.id)
        else:
            documents_reused += 1

    eval_cases_created = 0
    retrieval_items_created = 0
    for spec in POLICY_CASES:
        relevant_ids = [document_ids[item] for item in spec.relevant_documents]
        case = session.exec(
            select(EvalCase).where(
                EvalCase.category == ENTERPRISE_EVAL_KB_CODE,
                EvalCase.question == spec.question,
            )
        ).first()
        if case is None:
            case = EvalCase(
                question=spec.question,
                category=ENTERPRISE_EVAL_KB_CODE,
                expected_answer_keywords=list(spec.answer_keywords),
                expected_source_documents=[
                    document_titles[item] for item in spec.relevant_documents
                ],
                expected_chunk_ids=[],
                should_answer=True,
            )
            session.add(case)
            session.flush()
            eval_cases_created += 1
        else:
            case.expected_answer_keywords = list(spec.answer_keywords)
            case.expected_source_documents = [
                document_titles[item] for item in spec.relevant_documents
            ]
            case.enabled = True
            session.add(case)

        item = next(
            (
                candidate
                for candidate in session.exec(
                    select(RetrievalEvalItem).where(
                        RetrievalEvalItem.query == spec.question,
                    )
                ).all()
                if set(candidate.knowledge_base_ids or []) == {knowledge_base.id}
            ),
            None,
        )
        judgement = {
            "source": "enterprise_policy",
            "suite": ENTERPRISE_EVAL_SUITE,
            "case_key": spec.key,
            "difficulty": spec.difficulty,
            "tags": list(spec.tags),
            "gold_doc_count": len(relevant_ids),
        }
        if item is None:
            item = RetrievalEvalItem(
                eval_case_id=case.id,
                query=spec.question,
                knowledge_base_ids=[knowledge_base.id],
                relevant_document_ids=relevant_ids,
                relevant_chunk_ids=[],
                relevance_judgement=judgement,
            )
            session.add(item)
            retrieval_items_created += 1
        else:
            item.eval_case_id = case.id
            item.relevant_document_ids = relevant_ids
            item.relevant_chunk_ids = []
            item.relevance_judgement = judgement
            item.enabled = True
            session.add(item)

    session.commit()
    pending_index_ids = created_document_ids if indexer is not None else []
    warning = None
    if indexer is None:
        warning = "测试用例已生成，索引服务未连接；请启动后台索引后再评测。"

    return EnterpriseEvalSeedResult(
        knowledge_base_id=knowledge_base.id,
        suite=ENTERPRISE_EVAL_SUITE,
        documents_created=documents_created,
        documents_reused=documents_reused,
        retrieval_items_created=retrieval_items_created,
        eval_cases_created=eval_cases_created,
        index_queued=len(pending_index_ids),
        corpus_document_count=documents_created + documents_reused,
        case_count=len(POLICY_CASES),
        warning=warning,
        pending_index_document_ids=pending_index_ids,
    )
