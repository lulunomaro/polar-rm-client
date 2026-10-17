import subprocess,re,json,collections,pathlib
base='a119062'; tip='60bbc0007e3929d078032d65aa7364d2e7140a44'
paths=subprocess.check_output(['git','diff','--name-only','--diff-filter=ACMR',base,tip,'-z']).decode().strip('\0').split('\0')
patterns={
'private_key':rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----',
'github_token':rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})',
'openai_token':rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{30,}',
'slack_token':rb'\bxox[baprs]-[A-Za-z0-9-]{20,}',
'aws_access_key':rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
'jwt':rb'\beyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}',
'url_credential':rb'(?:https?|mqtt|postgres(?:ql)?|mysql)://[^\s/@:]+:[^\s/@]+@',
'auth_value':rb'(?i)(?:authorization\s*[:=]\s*[\"\x27]?bearer\s+|(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd)\s*[\"\x27]?\s*[:=]\s*[\"\x27])([A-Za-z0-9_+\-/=]{12,})',
}
findings=[];binary=[];total=0;sizes=[]
for path in paths:
 data=subprocess.check_output(['git','show',tip+':'+path]); total+=len(data);sizes.append((len(data),path))
 try:data.decode('utf-8')
 except UnicodeDecodeError:binary.append(path)
 if b'\0' in data:binary.append(path+' [NUL]')
 for label,pattern in patterns.items():
  for match in re.finditer(pattern,data):
   findings.append({'path':path,'line':data[:match.start()].count(b'\n')+1,'pattern':label,'matched_length':len(match.group())})
report={'base':base,'tip':tip,'changed_files':len(paths),'bytes':total,'extension_counts':dict(collections.Counter(pathlib.Path(p).suffix for p in paths)),'non_utf8_or_nul':binary,'sensitive_pattern_matches':findings,'largest_files':sorted(sizes,reverse=True)[:6]}
pathlib.Path('/tmp/review-publication-scan.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
