# Week 4: Virtual machines and containers

CC101 lecture notes, written for the Anchor demo (fictional course).

## Why virtualise?

One physical server is usually busy only a fraction of the time. Virtualisation lets one machine run many isolated workloads, so the hardware is shared and each workload can be started, stopped and moved on its own.

## Virtual machines

A **virtual machine (VM)** emulates a whole computer: virtual CPU, memory, disk and network card. A program called the **hypervisor** shares the real hardware between VMs.

- A **type 1 hypervisor** runs directly on the hardware (bare metal). Cloud providers use this kind.
- A **type 2 hypervisor** runs as an application on top of an ordinary operating system, for example a VM on your laptop.

Each VM boots its own full guest operating system. That makes VMs strongly isolated but heavy: a typical VM image is several gigabytes and takes tens of seconds to minutes to boot.

## Containers

A **container** packages an application with its libraries and settings, but shares the host's operating system kernel instead of booting its own. Containers are therefore much lighter: images are often tens or hundreds of megabytes, and a container starts in about a second.

The kernel keeps containers apart with two features:

- **namespaces** give each container its own view of processes, the network and the file system;
- **control groups (cgroups)** limit how much CPU and memory each container may use.

Because they share the kernel, containers are less isolated than VMs. A Linux container cannot run directly on a Windows kernel without a Linux VM underneath.

## Images and Dockerfiles

A container **image** is a read-only template; a **container** is a running instance of an image. Images are built in **layers**: each instruction in a Dockerfile adds one layer, and unchanged layers are reused from the cache on the next build.

```
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "server.py"]
```

Copy `requirements.txt` and install packages **before** copying the rest of the code: then editing your code does not invalidate the cached package layer, and rebuilds take seconds instead of minutes.

## VMs or containers?

| Question | Virtual machine | Container |
| --- | --- | --- |
| What is virtualised | The hardware | The operating system |
| Start-up time | Tens of seconds to minutes | About a second |
| Typical image size | Gigabytes | Megabytes |
| Isolation | Strong (own kernel) | Weaker (shared kernel) |
| Good for | Running a different OS, strict isolation | Packaging and scaling one application |

In practice they are combined: cloud providers run your containers inside VMs.

## Serverless containers

Some services run a container only while requests arrive and scale it to zero when idle, so you pay only while it works. The trade-off is a **cold start**: the first request after an idle period waits while a new instance starts. Nothing runs between requests, so background timers and in-memory data are lost when an instance stops.

## Check your understanding

1. Why does a container start faster than a VM?
2. Which kernel feature limits a container's memory?
3. Why should a Dockerfile install packages before copying the application code?
