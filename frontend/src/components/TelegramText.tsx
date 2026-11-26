import type { ReactNode } from 'react'
import type { TelegramEntity } from '../consoleTypes'

export default function TelegramText({text,entities=[]}:{text:string;entities?:TelegramEntity[]}) {
  // Telegram offsets and JS string slices both use UTF-16 code units.
  const boundaries=[...new Set([0,text.length,...entities.flatMap(e=>[e.offset,e.offset+e.length])])]
    .filter(offset=>offset>=0&&offset<=text.length).sort((a,b)=>a-b)
  return <>{boundaries.slice(0,-1).map((start,index)=>{
    const end=boundaries[index+1]
    let content:ReactNode=text.slice(start,end)
    for(const entity of entities.filter(e=>e.offset<=start&&e.offset+e.length>=end)) {
      const original=text.slice(entity.offset,entity.offset+entity.length)
      const href=entity._==='MessageEntityTextUrl'?entity.url:
        entity._==='MessageEntityUrl'?(/^https?:\/\//i.test(original)?original:'https://'+original):
        entity._==='MessageEntityMention'?'https://t.me/'+original.slice(1):
        entity._==='MessageEntityEmail'?'mailto:'+original:undefined
      if(href&&/^(https?:\/\/|tg:\/\/|mailto:)/i.test(href)) content=<a href={href} target="_blank" rel="noreferrer">{content}</a>
      else if(entity._==='MessageEntityBold') content=<strong>{content}</strong>
      else if(entity._==='MessageEntityItalic') content=<em>{content}</em>
      else if(entity._==='MessageEntityUnderline') content=<u>{content}</u>
      else if(entity._==='MessageEntityStrike') content=<s>{content}</s>
      else if(entity._==='MessageEntityCode'||entity._==='MessageEntityPre') content=<code>{content}</code>
      else if(entity._==='MessageEntityBlockquote') content=<span className="telegram-quote">{content}</span>
      else if(entity._==='MessageEntitySpoiler') content=<span className="telegram-spoiler">{content}</span>
    }
    return <span key={start}>{content}</span>
  })}</>
}
