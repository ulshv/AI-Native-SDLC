# Managed by ai-native-sdlc platform/bootstrap.sh. Installed as /etc/apparmor.d/cri-containerd.apparmor.d.
#
# containerd's default AppArmor profile for containers, rendered from contrib/apparmor/template.go
# of containerd v2.3.4 (the version in k3s v1.36.5+k3s1) with the values containerd uses on this
# host, plus the two rules marked below. containerd loads its own profile only when none of this
# name is loaded, and apparmor.service loads this file at boot, before k3s starts.
#
# Why: on Ubuntu with kernel.apparmor_restrict_unprivileged_unconfined=1 (the default since the
# 7.0.0-42 kernel), runc's switch to this profile becomes a stack, so container processes run as
# "cri-containerd.apparmor.d//&unconfined". The template allows signals and ptrace only between
# peers named exactly "cri-containerd.apparmor.d", so processes in one container can no longer
# signal each other: Postgres latches (SIGURG), Go preemption (SIGURG), Bun's thread suspension
# (SIGPWR). The added rules are the narrow ones proposed upstream; they allow the same peers as
# before, only under their stacked label. Nothing else changes.
#   https://github.com/containerd/containerd/issues/14485
#   https://github.com/containerd/containerd/pull/14487
# Remove this file once k3s ships a containerd that contains that fix (see platform/README.md).

abi <abi/3.0>,

#include <tunables/global>

profile cri-containerd.apparmor.d flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>

  network,
  capability,
  file,
  umount,
  # Host (privileged) processes may send signals to container processes.
  signal (receive) peer=unconfined,
  # runc may send signals to container processes.
  signal (receive) peer=runc,
  # crun may send signals to container processes.
  signal (receive) peer=crun,
  # Manager may send signals to container processes.
  signal (receive) peer=unconfined,
  # Container processes may send signals amongst themselves.
  signal (send,receive) peer=cri-containerd.apparmor.d,
  # ai-native-sdlc: the same, under the stacked label (containerd#14485).
  signal (send,receive) peer=cri-containerd.apparmor.d//&unconfined,

  deny @{PROC}/* w,   # deny write for all files directly in /proc (not in a subdir)
  # deny write to files not in /proc/<number>/** or /proc/sys/**
  deny @{PROC}/{[^1-9/],[^1-9/][^0-9/],[^1-9s/][^0-9y/][^0-9s/],[^1-9/][^0-9/][^0-9/][^0-9/]*}/** w,
  deny @{PROC}/sys/[^k]** w,  # deny /proc/sys except /proc/sys/k* (effectively /proc/sys/kernel)
  deny @{PROC}/sys/kernel/{?,??,[^s][^h][^m]**} w,  # deny everything except shm* in /proc/sys/kernel/
  deny @{PROC}/sysrq-trigger rwklx,
  deny @{PROC}/kcore rwklx,

  deny mount,

  deny /sys/[^f]*/** wklx,
  deny /sys/f[^s]*/** wklx,
  deny /sys/fs/[^c]*/** wklx,
  deny /sys/fs/c[^g]*/** wklx,
  deny /sys/fs/cg[^r]*/** wklx,
  deny /sys/firmware/** rwklx,
  deny /sys/devices/virtual/powercap/** rwklx,
  deny /sys/kernel/security/** rwklx,

  # allow processes within the container to trace each other,
  # provided all other LSM and yama setting allow it.
  ptrace (trace,tracedby,read,readby) peer=cri-containerd.apparmor.d,
  # ai-native-sdlc: the same, under the stacked label (containerd#14485).
  ptrace (trace,tracedby,read,readby) peer=cri-containerd.apparmor.d//&unconfined,
}
