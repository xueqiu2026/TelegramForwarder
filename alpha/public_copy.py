"""Shared checks for editorial process text in public drafts, not business restrictions."""
import re


# Match writing/source-comparison language, never generic words such as gas or deadline.
_PROCESS = re.compile(
    r'资料存在冲突|暂不能统一表述|另一条消息|原文称|本条资料|相关信息链接|两者可能涉及不同统计口径|【冲突】'
    r'|(?:尚不能|不能|无法|暂不能|暂无法|尚无法)(?:对应为|转换为|换算为?|转换成|换算成)(?:北京时间|UTC)'
    r'|(?:适用)?时区(?:待公布|待确认|未注明|未提供)|以上为预告日期'
    r'|(?:原文|源文|源消息|输入资料|所给资料|现有资料|采集资料|提供的资料)[^。！？\n]{0,24}(?:未写|没写|未提供|没有提供|未注明|未明确|未说明|缺少|未给出)'
    r'|(?:两条|多条|不同)(?:来源|消息)[^。！？\n]{0,24}(?:冲突|不一致|差异|口径|统一)'
    r'|(?:已|未|没有)(?:读取|核验|核对|访问)(?:原文|来源|网页|图片|官网)'
    r'|(?:本次|本文|本稿)[^。！？\n]{0,12}(?:翻译|整理时|保留可确认|省略了|不做推测)'
    r'|(?:以下是|下面是|这是)(?:整理后|翻译后|优化后|润色后|根据原文整理)[^。！？\n]{0,12}(?:文案|中文|正文|版本)'
    r'|(?:internal_notes|context_gaps|type_reason|deadline_utc)\s*[:：=]'
    r'|(?:^|\n)\s*(?:\*\*|#{1,3}\s*)?(?:内部备注|内部说明|翻译说明|译者注|核对说明|审稿说明|写作说明|思考过程|推理过程|思考|分析过程|资料缺口)\s*(?:\*\*)?\s*[:：]'
    r'|</?(?:think|analysis|reasoning)>'
    r'|(?:cannot|unable to)\s+(?:reliably\s+)?convert[^.\n]{0,40}(?:Beijing|UTC)'
    r'|(?:the (?:source|original) (?:text|message)|provided material)[^.\n]{0,25}(?:does not|doesn.t|did not)\s+(?:specify|provide|mention)',
    re.I | re.M,
)


def has_process_note(draft):
    return bool(_PROCESS.search(draft or ''))


def validate_public_copy(draft):
    if has_process_note(draft):
        raise ValueError('公开稿含翻译、核对或写作过程说明，待重新生成')
