$proc_info = New-CimInstance -CimClass (Get-CimClass -ClassName Win32_ProcessStartup) -Property @{CreateFlags=16777216} -ClientOnly
$arguments = @{CommandLine="python C:\Users\Bob\CelesteTAS-Improvements-Tracker\bot.py"; ProcessStartupInformation=$proc_info}
Invoke-CimMethod -ClassName Win32_Process -Name Create -Arguments $arguments