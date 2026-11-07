# 关闭生词本后台服务
# 只杀「命令行里带 wordbook.py」的 python 进程，
# 不再按端口范围一刀切——否则你开在 8770 的 Jupyter 会被连坐。
$killed = 0
$procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue
foreach ($p in $procs) {
    if ($p.CommandLine -and $p.CommandLine -like '*wordbook.py*') {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("  已停止 PID={0}" -f $p.ProcessId)
        $killed++
    }
}
if ($killed -eq 0) {
    Write-Host "  没有正在运行的生词本服务。"
} else {
    Write-Host ("  共关闭 {0} 个进程。" -f $killed)
}
