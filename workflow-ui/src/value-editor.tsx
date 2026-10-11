import React, {useEffect, useId, useState} from "react";

type Props = {label:string; value:any; change:(value:any)=>void; nodes:any[]; fields?:Record<string,string[]>; allowResult?:boolean; allowConstant?:boolean; disabled?:boolean};
export function ValueInput({label,value,change,nodes,fields={},allowResult=false,allowConstant=true,disabled=false}:Props) {
  const path = value && typeof value === "object" ? String(value.$ref || "") : "";
  const source = !path ? "constant" : path === "user" ? "user" : path.startsWith("last_output") ? "previous" : path.startsWith("outputs.") ? "node" : path.startsWith("result") && allowResult ? "result" : "custom";
  const selectedId = source === "node" ? path.split(".")[1] : nodes[0]?.id || "";
  const prefix = source === "previous" ? "last_output" : source === "result" ? "result" : `outputs.${selectedId}`;
  const field = path === prefix ? "" : path.slice(prefix.length + 1);
  const node = nodes.find(n => n.id === selectedId);
  const options = source === "node" ? fields[node?.id] || fields[node?.type] || [] : Array.from(new Set(Object.values(fields).flat()));
  const id = useId();
  const setField = (next:string) => change({$ref:prefix + (next.trim() ? "." + next.trim() : "")});
  return <div className="value-input">
    <label>{label}<select aria-label={`${label} kaynağı`} disabled={disabled} value={source} onChange={e => {
      const mode=e.target.value;
      change(mode === "constant" ? "" : {$ref:mode === "user" ? "user" : mode === "previous" ? "last_output.text" : mode === "result" ? "result.text" : mode === "node" ? `outputs.${nodes[0]?.id || "node"}.text` : "last_result.status"});
    }}>
      <option value="user">Kullanıcı metni</option><option value="previous">Önceki node çıktısı</option><option value="node">Belirli node çıktısı</option>
      {allowResult && <option value="result">Bu node’un doğal sonucu</option>}
      {allowConstant && <option value="constant">Sabit metin</option>}<option value="custom">Özel alan yolu</option>
    </select></label>
    {source === "constant" && <label>Sabit metin<textarea aria-label={`${label} metni`} disabled={disabled} value={typeof value === "string" ? value : ""} onChange={e=>change(e.target.value)}/></label>}
    {source === "node" && <label>Node<select aria-label={`${label} node’u`} disabled={disabled} value={selectedId} onChange={e=>change({$ref:`outputs.${e.target.value}.text`})}>
      {!nodes.length && <option value="">Node yok</option>}{nodes.map(n=><option key={n.id} value={n.id}>{n.data.name || n.id}</option>)}
    </select></label>}
    {["previous","node","result"].includes(source) && <label>Çıkış alanı<input aria-label={`${label} alanı`} list={id} disabled={disabled} value={field} placeholder="text · results.0.text · boş: tüm çıkış" onChange={e=>setField(e.target.value)}/>
      <datalist id={id}>{options.map(f=><option key={f} value={f}/>)}</datalist><small>Liste elemanı için örnek: results.0.text. Boş alan tüm çıkışı seçer.</small></label>}
    {source === "custom" && <label>Alan yolu<input aria-label={`${label} alan yolu`} disabled={disabled} value={path} onChange={e=>change({$ref:e.target.value})}/></label>}
  </div>;
}

export function OutputEditor({value,change,nodes,fields,disabled,reportError}: {value:any; change:(v:any)=>void; nodes:any[]; fields?:Record<string,string[]>; disabled:boolean; reportError:(error:string)=>void}) {
  const mode = value?.mode || "raw";
  const serialized = JSON.stringify(value?.value ?? {cevap:{$ref:"result.text"}}, null, 2);
  const [text,setText] = useState(serialized), [error,setError] = useState("");
  useEffect(()=>{setText(serialized);setError("");},[serialized,mode]);
  useEffect(()=>{reportError(error);},[error,reportError]);
  useEffect(()=>()=>reportError(""),[reportError]);
  return <section className="node-output-editor"><h4>Çıkış sonucu</h4>
    <label>Sonuç türü<select aria-label="Çıkış sonucu türü" disabled={disabled} value={mode} onChange={e=>change({mode:e.target.value,...(["raw","user"].includes(e.target.value) ? {} : {value:e.target.value === "text" ? {$ref:"result.text"} : {cevap:{$ref:"result.text"}}})})}>
      <option value="raw">Doğal sonuç</option><option value="user">Kullanıcı sözü</option><option value="text">Özel metin</option><option value="json">Özel JSON</option>
    </select></label>
    {mode === "user" && <small>Kullanıcının son söylediği metin çıkışın text alanına yazılır. Sonraki node’da last_output.text ile kullanılabilir.</small>}
    {mode === "text" && <ValueInput label="Çıkış değeri" value={value.value} change={next=>change({...value,value:next})} nodes={nodes} fields={fields} allowResult disabled={disabled}/>}
    {mode === "json" && <label>JSON çıkışı<textarea aria-label="JSON çıkışı" aria-invalid={!!error} rows={6} disabled={disabled} value={text} onChange={e=>{setText(e.target.value);try {JSON.parse(e.target.value);setError("");}catch {setError("Geçersiz JSON çıkışı. Kaydetmeden önce düzeltin.");}}} onBlur={()=>{
      try {change({...value,value:JSON.parse(text)});setError("");} catch {setError("Geçersiz JSON çıkışı. Kaydetmeden önce düzeltin.");}
    }}/><small>{error || 'Alan başvurusu: {"cevap":{"$ref":"result.text"}}. Sabit değerler de kullanılabilir.'}</small></label>}
    <small>Sonraki node: last_output · Belirli node: outputs.node_id. Çıkış konuşma metnini değiştirmez.</small>
  </section>;
}
