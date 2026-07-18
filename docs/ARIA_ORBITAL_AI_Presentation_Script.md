# Orbital AI & ARIA: Presentation Script

**Purpose:** Engineering team briefing / investor technical deep-dive
**Presenter:** Robert Christopher, Founder — StageGate
**URL:** [stagegate.space](https://stagegate.space)
**Estimated Delivery Time:** 25–35 minutes

---

## Opening

Good [morning/afternoon]. I want to walk you through exactly how we are going to build Orbital AI and ARIA — not the marketing story, but the actual engineering plan. By the end of this session, you will understand the three environments we are deploying, the seven modules we are building, which components we own internally, which we are adopting from open-source, and what the first 18 weeks of development look like in concrete terms.

Let me start with the core premise, because it is the foundation for every technical decision we are making.

---

## Section 1: The Core Premise

Every robotics platform that exists today — Formant, Foxglove, InOrbit — is built on the same flawed assumption. They all trust the robot's own sensors. They receive telemetry from the robot, process it, and display it back to you. The problem is that the robot's internal SLAM is wrong. Odometric drift, perceptual aliasing, sensor degradation — these are not edge cases. They are the normal operating state of every autonomous robot in a real-world environment.

StageGate does not trust the robot. ARIA generates its own independent ground truth by deploying an overhead camera network that tracks every robot in the facility from the outside. We then use that ground truth to actively correct the robot's trajectory in real time, at 10 times per second, via its own native API.

This is the shift from passive observability to active orchestration. And it is the reason our three patent filings are defensible — because no competitor can replicate this without building the same physical infrastructure.

---

## Section 2: The Three-Environment Architecture

The system runs across three distinct environments, and it is critical that the team understands the boundaries between them.

**Environment 1 is the Orbital AI Cloud**, running on AWS or GCP. This is where the Global Spatial Map lives, where the Mission Planner generates high-level trajectories, where the Benchmark Library database stores cross-vendor performance data, and where the OEM Licensing API exposes that data to paying customers. This is the only environment with public internet access.

**Environment 2 is the ARIA Edge Node**, deployed on-premise at the customer site. This is an air-gapped GPU server. It has no direct internet access. It processes the camera feeds, runs the computer vision pipeline, executes the TF Hijack loop, and sends commands to the robots over the local private network. It communicates with the cloud only via a secured, outbound-only mTLS tunnel.

**Environment 3 is the Robot Subnet**, a VLAN-isolated network containing all the robots. No robot has direct internet access. The ARIA Edge Node is the only node authorized to publish to robot command topics. This is how we enforce the Zero Trust security architecture.

The reason we separate these three environments is not just security — it is latency. The injection loop must run at 10Hz. If we were routing waypoint commands through the cloud, we would introduce 50–200ms of round-trip latency. That breaks the loop. By keeping the injection logic on the edge, the round-trip from camera frame to robot command is under 10 milliseconds.

---

## Section 3: The Seven Modules

The build is organized into seven modules. Let me walk through each one.

**Module 1 is the Computer Vision Pipeline.** This runs on the ARIA Edge Node and is responsible for computing the absolute pose of every robot in the facility — what we call P-external. In Phase 1, we are using ArUco markers. Each robot in the development fleet gets a 15-centimeter printed marker on its top surface. OpenCV detects the marker, estimates the pose relative to the camera, and then we apply the camera's known extrinsic calibration to project that pose into global facility coordinates. In Phase 2, we replace the markers with a trained YOLOv8 model so we can track any robot without modifying the hardware. Phase 1 gets us to a working system fast. Phase 2 is what we need for production deployments.

**Module 2 is the Micro-Waypoint Generator — the TF Hijack.** This is the core of ARIA and the primary patent-protected IP. Every ROS 2 robot maintains a transform chain from the map frame to the odometry frame to the robot's base. By default, the robot's internal SLAM publishes that map-to-odom transform. ARIA suppresses that publisher and takes it over. We then calculate the drift delta — the difference between where the robot thinks it is and where it actually is — and use that delta to transform the next target coordinate into the robot's flawed internal frame. The robot then navigates to what it thinks is the correct location, which is physically the right place. This loop runs at 10Hz. The lookahead distance is 20 centimeters by default, which creates a smooth, continuous correction without jerky movements.

**Module 3 is the Fleet Adapter layer.** This is where we handle the reality that every robot vendor uses a different protocol. Unitree uses ROS 2 nav topics. Boston Dynamics uses gRPC with a proprietary lease system. Agility Robotics uses a cloud REST API called Arc. We have built a base adapter interface that defines four required methods — connect, inject waypoint, get internal pose, and trigger E-Stop — and each vendor adapter implements that interface. This means the Waypoint Generator does not need to know which robot it is talking to. It just calls inject waypoint, and the adapter handles the translation.

**Module 4 is the Safety Halt Controller.** This is the out-of-band failsafe that runs as a completely independent process, separate from the injection loop. It monitors the drift delta for every active robot at 20Hz — twice the injection rate. If the delta exceeds 50 centimeters, or if the external cameras detect physical movement while the robot reports it is stationary — which is the signature of a sensor blinding or hijack attack — the controller bypasses the robot's internal software entirely and triggers a hardware E-Stop via the Fleet Adapter. This is what makes ARIA defensible against zero-day exploits. Standard E-Stops rely on the robot's internal software. If that software is compromised, the E-Stop fails. Ours does not.

**Module 5 is the Benchmark Library Pipeline.** Every drift delta calculation we make gets written to InfluxDB with tags for robot ID, vendor, model, and facility. From this time-series data, we compute three core metrics: Mean Time Between Degradation, which tells us how long a robot operates cleanly before its SLAM starts drifting; Recovery Latency, which tells us how quickly it recovers; and the Environmental Degradation Score, which normalizes the drift rate against a baseline to compare performance across different facility conditions. This is the data product we license back to OEMs.

**Module 6 is the Cloud Orchestration Layer.** The Orbital AI cloud exposes a REST API that the ARIA Edge Node polls for updated mission assignments and trajectory data. It also receives telemetry from the edge node and stores it in the Benchmark Library. The cloud layer is built on FastAPI and deployed on AWS.

**Module 7 is the Fleet Management Dashboard.** This is the only module we are contracting out to a third-party agency, because it does not contain core robotics IP. The dashboard is a React and Next.js frontend that ingests data from our cloud API. The key design requirements are the amber orange accent color for all CTAs, industry-specific tabs for Humanoids, Cleaning, Delivery, and Inventory, and the CRM business card panel that gives operators an AI-generated brief on each robot model and its OEM.

---

## Section 4: Build vs. Buy vs. Contract

Let me be explicit about the three-track strategy, because this is where we protect the IP while moving fast.

We build in-house everything that is core to the patent portfolio: the CV pipeline, the TF Hijack loop, the Safety Halt Controller, the Fleet Adapters, and the Benchmark Library pipeline. These components are the moat. They never leave our codebase.

We adopt open-source for the commodity robotics infrastructure. Open-RMF handles high-level traffic scheduling — conflict resolution between robots at intersections. ROS 2 is the middleware. CycloneDDS is the data distribution service. MCAP is the logging format. We do not build any of this. We integrate it.

We contract out the Fleet Dashboard, the cloud database infrastructure, and the third-party security audit. These are important but not secret. A good web agency can build the dashboard in six to eight weeks given our API contract. A cloud architect can set up the AWS infrastructure in two weeks. A security firm can validate the mTLS and SROS2 implementation in four weeks.

---

## Section 5: The Sprint Plan

We have 18 weeks of work organized into four sprints.

Sprint 1 is four weeks and has one goal: a working end-to-end injection loop with one Unitree G1 robot in the lab. We set up the hardware, calibrate the cameras, implement the CV pipeline with ArUco detection, build the Unitree adapter, implement the TF Hijack loop, and validate 10Hz latency end-to-end. Everything else is secondary until this works.

Sprint 2 adds AgiBot to the fleet, integrates Open-RMF as the traffic scheduler above ARIA, and begins collecting Benchmark Library data in InfluxDB. We also deploy the Orbital AI cloud backend in this sprint.

Sprint 3 validates the command-level override with Boston Dynamics Spot via gRPC, launches the internal dashboard, and implements the full cybersecurity stack — SROS2, mTLS, and the third-party security audit.

Sprint 4 completes the remaining vendor adapters, trains the YOLOv8 markerless detection model, and deploys the full hardware stack at the first customer pilot site.

---

## Section 6: Hardware Requirements

Before Sprint 1 can begin, nine hardware items need to be ordered. The most critical are the industrial PoE cameras — we need a minimum of eight Basler or FLIR global-shutter units. Consumer cameras will not work because their video compression introduces latency that breaks the injection loop. The ARIA Edge Node must be a GPU-accelerated server with an NVIDIA RTX 4000 or Jetson AGX Orin. The network infrastructure requires a managed PoE+ switch for VLAN segmentation and dedicated Wi-Fi 6 access points that are completely isolated from the customer's corporate network. And we need the Unitree G1 and AgiBot A2 as the first development robots.

---

## Closing

The full developer build guide is available at the internal engineering repository. It contains the complete code skeletons for all seven modules, the exact bash commands to set up the development environment, the vendor adapter API reference table, the InfluxDB schema, the cloud API contract, and the hardware checklist with specific model numbers.

The URL for all external-facing StageGate properties is **stagegate.space**. The internal engineering repository will be hosted at **github.com/stagegate-space/aria-core**.

The first working injection loop — one robot, one camera, 10Hz, in the lab — is the only milestone that matters in the first four weeks. Everything else is secondary to proving that the core loop works.

Questions?

---

*StageGate Confidential — Engineering Internal*
*[stagegate.space](https://stagegate.space) | partners@stagegate.space | San Francisco, CA*
