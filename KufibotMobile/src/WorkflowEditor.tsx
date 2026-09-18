import React, {useEffect, useRef} from 'react';
import {Modal} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';
import {WebView} from 'react-native-webview';
import * as DocumentPicker from 'expo-document-picker';
import {Robot} from './discovery';
import {State} from './useRobot';

export function WorkflowEditor({visible,robot,state,event,send,onClose,subscribe}: {
  visible:boolean;robot:Robot;state:State|null;event:any;
  send:(data:{type:string;[key:string]:unknown})=>unknown;onClose:()=>void;
  subscribe:(listener:(event:any)=>void)=>(()=>void);
}) {
  const web=useRef<WebView>(null),latest=useRef(state);
  latest.current=state;
  const origin=`http://${robot.host}:${robot.port}`;
  const publish=(value:any)=>web.current?.injectJavaScript(`window.kufibotWorkflowReceive?.(${JSON.stringify(value)});true;`);
  useEffect(()=>{if(visible&&state)publish(state)},[visible,state]);
  useEffect(()=>subscribe(value=>{if(visible)publish(value)}),[visible,subscribe]);
  async function pick(value:any) {
    try {
      if(!latest.current?.owner)throw new Error('Kumanda sahipliği gerekli');
      if(!/^[a-zA-Z0-9_-]{1,64}$/.test(value.collection))throw new Error('Geçersiz koleksiyon');
      const result=await DocumentPicker.getDocumentAsync({type:['application/pdf','text/csv','text/comma-separated-values','application/json'],copyToCacheDirectory:true});
      if(result.canceled)return;
      const file=result.assets[0];
      if((file.size||0)>20*1024*1024)throw new Error('Dosya 20 MiB sınırını aşıyor');
      // The system picker may background the app; allow ownership to reconnect.
      const deadline=Date.now()+8000;
      while(!latest.current?.owner&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,100));
      const token=latest.current?.workflowToken;
      if(!latest.current?.owner||!token)throw new Error('Robot bağlantısını yenileyin');
      const form=new FormData();
      form.append('file',{uri:file.uri,name:file.name,type:file.mimeType||'application/octet-stream'} as any);
      const response=await fetch(`${origin}/api/knowledge/${value.collection}/upload?delimiter=${encodeURIComponent(value.delimiter||'')}`,
        {method:'POST',headers:{Authorization:`Bearer ${token}`},body:form});
      if(!response.ok)throw new Error(await response.text());
      publish({type:'workflowUpload',result:await response.json()});
    }catch(error){publish({type:'workflowUpload',error:String(error)})}
  }
  if(!visible)return null;
  const close=()=>{send({type:'workflow',action:'testStop'});onClose()};
  return <Modal visible animationType="slide" onRequestClose={close}><SafeAreaView style={{flex:1,backgroundColor:'#101721'}}>
    <WebView ref={web} source={{uri:`${origin}/workflows?native=1`}} originWhitelist={[origin]}
      onShouldStartLoadWithRequest={r=>r.url.startsWith(`${origin}/`)} onLoadEnd={()=>state&&publish(state)}
      onMessage={e=>{try{const value=JSON.parse(e.nativeEvent.data);if(value.type==='workflowClose')close();
        if(value.type==='workflowCommand'&&value.command?.type==='workflow')send(value.command);
        if(value.type==='workflowPickFile')void pick(value);
      }catch{/* Ignore malformed bridge messages. */}}}/>
  </SafeAreaView></Modal>;
}
