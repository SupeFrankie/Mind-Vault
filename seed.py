"""Database seed script for demo & presentation purposes.

Populates MindVault with a realistic student account, courses, units,
stored vault materials, active AI Study Assistant guides, flashcards,
interactive quizzes, group tasks, and conversational Q&A threads.

Run via:
    python seed.py
"""
import os
import sys
from datetime import date, timedelta

# Initialize Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
import django

django.setup()

from django.contrib.auth import get_user_model
from apps.vault.models import Semester, Course, Unit, Tag, Material
from apps.sharing.models import StudyGroup, Membership
from apps.study_assistant.models import (
    StudyDocument,
    GeneratedNote,
    Flashcard,
    QuizQuestion,
    GroupStudyResource,
    GroupSessionTask,
    StudyChatMessage,
)

User = get_user_model()


def seed():
    print("=" * 60)
    print("🌱  Seeding MindVault presentation data...")
    print("=" * 60)

    # 1. Create or update Demo User
    demo_email = "demo@mindvault.edu"
    demo_password = "Password123!"
    user, created = User.objects.get_or_create(
        email=demo_email,
        defaults={
            "full_name": "Alex Rivera",
            "university": "Stanford University",
        },
    )
    user.set_password(demo_password)
    user.full_name = "Alex Rivera"
    user.university = "Stanford University"
    user.save()
    print(f"✓ Demo Account ready: {user.email} (Password: {demo_password})")

    # Secondary classmate for group collaborations
    peer, _ = User.objects.get_or_create(
        email="sarah.chen@mindvault.edu",
        defaults={
            "full_name": "Sarah Chen",
            "university": "Stanford University",
        },
    )
    peer.set_password("Password123!")
    peer.save()

    # 2. Semesters
    sem_fall, _ = Semester.objects.get_or_create(owner=user, name="Fall 2026")
    sem_spring, _ = Semester.objects.get_or_create(owner=user, name="Spring 2026")
    print("✓ Semesters created (Fall 2026, Spring 2026)")

    # 3. Courses
    cs301, _ = Course.objects.get_or_create(
        semester=sem_fall,
        title="Distributed Systems & Consensus",
        defaults={"code": "CS 301"},
    )
    cs240, _ = Course.objects.get_or_create(
        semester=sem_fall,
        title="Advanced Operating Systems",
        defaults={"code": "CS 240"},
    )
    cs145, _ = Course.objects.get_or_create(
        semester=sem_spring,
        title="Modern Database Architecture",
        defaults={"code": "CS 145"},
    )
    print("✓ Courses created (CS 301, CS 240, CS 145)")

    # 4. Units
    u_raft, _ = Unit.objects.get_or_create(course=cs301, title="Unit 1: Raft & Paxos Consensus", defaults={"order": 1})
    u_bft, _ = Unit.objects.get_or_create(course=cs301, title="Unit 2: Byzantine Fault Tolerance", defaults={"order": 2})
    u_locks, _ = Unit.objects.get_or_create(course=cs240, title="Unit 1: Concurrency & Deadlocks", defaults={"order": 1})
    u_btree, _ = Unit.objects.get_or_create(course=cs145, title="Unit 1: B-Trees & LSM Storage", defaults={"order": 1})
    print("✓ Course Units created")

    # 5. Tags
    tag_core, _ = Tag.objects.get_or_create(owner=user, name="Core CS")
    tag_midterm, _ = Tag.objects.get_or_create(owner=user, name="Midterm Prep")
    tag_systems, _ = Tag.objects.get_or_create(owner=user, name="Systems")

    # 6. Vault Materials
    mat_raft, _ = Material.objects.get_or_create(
        owner=user,
        unit=u_raft,
        title="Raft Consensus Protocol & Leader Election",
        defaults={
            "kind": Material.Kind.NOTE,
            "sensitivity": Material.Sensitivity.INTERNAL,
            "notes": (
                "Raft is a consensus algorithm designed for understandability and fault tolerance.\n"
                "It maintains state machine replication across a cluster of N servers, tolerating up to (N-1)/2 failures.\n\n"
                "Key Mechanisms:\n"
                "- Node States: Follower, Candidate, Leader.\n"
                "- Randomized Election Timeouts: Typically 150ms to 300ms to eliminate split-vote deadlocks.\n"
                "- Term Numbers: Logical clocks that act as arbitrary monotonically increasing integers to detect obsolete information.\n"
                "- Log Replication: Only the leader accepts write proposals. Entries are appended and committed once replicated on a majority of nodes.\n"
                "- Leader Completeness: If a log entry is committed in a given term, that entry will be present in the logs of the leaders for all higher terms."
            ),
        },
    )
    mat_raft.tags.add(tag_core, tag_midterm, tag_systems)

    mat_deadlock, _ = Material.objects.get_or_create(
        owner=user,
        unit=u_locks,
        title="Deadlock Prevention & Banker's Algorithm",
        defaults={
            "kind": Material.Kind.NOTE,
            "sensitivity": Material.Sensitivity.INTERNAL,
            "notes": (
                "Deadlocks occur when a set of processes are blocked because each process is holding a resource and waiting for another.\n"
                "Coffman Conditions (all 4 must hold simultaneously):\n"
                "1. Mutual Exclusion: non-shareable resources.\n"
                "2. Hold and Wait: processes hold resources while requesting more.\n"
                "3. No Preemption: resources cannot be forcibly seized.\n"
                "4. Circular Wait: closed loop of waiting processes.\n\n"
                "Banker's Algorithm tests for safety by simulating the allocation of predetermined maximum possible amounts of all resources."
            ),
        },
    )
    mat_deadlock.tags.add(tag_core, tag_systems)

    mat_lsm, _ = Material.objects.get_or_create(
        owner=user,
        unit=u_btree,
        title="B-Tree vs LSM-Tree Storage Engines",
        defaults={
            "kind": Material.Kind.RESEARCH,
            "sensitivity": Material.Sensitivity.PUBLIC,
            "authors": "Patrick O'Neil, Edward O'Neil, Gerhard Weikum",
            "publication_year": 1996,
            "notes": (
                "Comparison of traditional B+ Tree storage engines against Log-Structured Merge (LSM) Trees.\n"
                "- B-Trees optimize for fast random reads via tree traversal (O(log N)), but incur write amplification and random I/O.\n"
                "- LSM-Trees buffer incoming writes sequentially into an in-memory MemTable (SSTables on disk), achieving high write throughput.\n"
                "- Compaction algorithms (Size-Tiered vs Leveled) mitigate read degradation and reclaim space from tombstones."
            ),
        },
    )
    print("✓ Vault Materials created with realistic academic notes")

    # 7. Study Group
    group, _ = StudyGroup.objects.get_or_create(
        name="Distributed Systems Study Circle",
        defaults={"owner": user},
    )
    Membership.objects.get_or_create(group=group, user=user, defaults={"role": Membership.Role.OWNER})
    Membership.objects.get_or_create(group=group, user=peer, defaults={"role": Membership.Role.MEMBER})
    print(f"✓ Study Group created: '{group.name}' with join code: {group.join_code}")

    # 8. AI Study Assistant Document & Resources (for Presentation)
    doc, _ = StudyDocument.objects.get_or_create(
        user=user,
        material=mat_raft,
        title="Raft Consensus Protocol & Leader Election",
        defaults={
            "group": group,
            "status": StudyDocument.Status.COMPLETED,
            "extracted_text": mat_raft.notes,
            "raw_text": mat_raft.notes,
        },
    )

    # Generated Notes
    GeneratedNote.objects.update_or_create(
        document=doc,
        defaults={
            "summary": (
                "Raft decomposes distributed consensus into three distinct, manageable subproblems: "
                "Leader Election, Log Replication, and Safety. It guarantees equivalent safety to Multi-Paxos "
                "while significantly reducing state space and architectural complexity."
            ),
            "explanation": (
                "In Raft, consensus relies on an elected leader taking full responsibility for managing the replicated log. "
                "The leader accepts client requests, replicates them to followers, and instructs state machines when it is safe "
                "to apply them. Randomized election timeouts (150ms-300ms) prevent split votes, while terms serve as logical clocks "
                "ensuring outdated leaders are immediately superseded without cluster partitions."
            ),
            "structured_notes": [
                "Server states: Every node begins as a Follower. If it receives no heartbeats within its randomized election timeout, it becomes a Candidate.",
                "Election mechanics: Candidates increment their term number, vote for themselves, and broadcast RequestVote RPCs to acquire a strict majority (N/2 + 1).",
                "Log flow direction: Log entries flow strictly from Leader to Followers; a leader never truncates or rewrites its own entries.",
                "Commit condition: An entry is safely committed once it is replicated across a majority of servers in the active term.",
                "Leader Completeness: Future leaders are mathematically guaranteed to contain all previously committed log entries.",
            ],
        },
    )

    # Flashcards (5 high-yield 3D flashcards)
    doc.flashcards.all().delete()
    Flashcard.objects.bulk_create([
        Flashcard(
            document=doc,
            question="What are the three operational states a Raft node can occupy?",
            answer="Follower, Candidate, and Leader.",
            order=0,
        ),
        Flashcard(
            document=doc,
            question="Why does Raft enforce randomized election timeouts (e.g. 150ms–300ms)?",
            answer="To prevent split-vote scenarios where multiple candidates start elections simultaneously and split the votes evenly.",
            order=1,
        ),
        Flashcard(
            document=doc,
            question="What formula defines a valid quorum in an N-node Raft cluster?",
            answer="A strict majority: floor(N/2) + 1. For instance, in a 5-node cluster, at least 3 nodes must agree.",
            order=2,
        ),
        Flashcard(
            document=doc,
            question="What is the Log Matching Property in Raft?",
            answer="If two entries in distinct logs share the same index and term, they store the same command and all preceding entries are identical.",
            order=3,
        ),
        Flashcard(
            document=doc,
            question="Can a newly elected leader directly commit entries from an older term by counting replicas?",
            answer="No, Raft prevents directly committing older entries by replica count; they are committed indirectly once a current-term entry commits.",
            order=4,
        ),
    ])

    # Quizzes (3 interactive multiple-choice questions)
    doc.quiz_questions.all().delete()
    QuizQuestion.objects.bulk_create([
        QuizQuestion(
            document=doc,
            question="Which quorum requirement must a Candidate satisfy to become Leader in a 5-node cluster?",
            options=[
                "A) All 5 nodes must confirm",
                "B) At least 3 nodes (majority)",
                "C) At least 2 nodes",
                "D) Only the node with highest CPU",
            ],
            correct_answer="B) At least 3 nodes (majority)",
            explanation="Raft requires a simple majority (floor(5/2) + 1 = 3) to guarantee that no two leaders can be elected in the same term.",
            order=0,
        ),
        QuizQuestion(
            document=doc,
            question="What happens when a follower receives an AppendEntries RPC with a term lower than its own current term?",
            options=[
                "A) It accepts the entries immediately",
                "B) It shuts down and restarts",
                "C) It rejects the RPC and returns its own higher term",
                "D) It delegates to another follower",
            ],
            correct_answer="C) It rejects the RPC and returns its own higher term",
            explanation="Followers reject requests from stale leaders, causing the stale leader to step down and update its current term.",
            order=1,
        ),
        QuizQuestion(
            document=doc,
            question="How does Raft resolve conflicting uncommitted log entries on a follower?",
            options=[
                "A) Follower keeps its version",
                "B) Leader decrements nextIndex, locates the last match, and overwrites conflicting follower entries",
                "C) The entire log is deleted on all nodes",
                "D) A split vote is immediately triggered",
            ],
            correct_answer="B) Leader decrements nextIndex, locates the last match, and overwrites conflicting follower entries",
            explanation="The leader enforces log consistency by walking back nextIndex until follower agreement is reached, then overwriting discrepancies.",
            order=2,
        ),
    ])

    # Group Study Resource & Tasks
    group_res, _ = GroupStudyResource.objects.update_or_create(
        document=doc,
        defaults={
            "revision_summary": (
                "Group study priority: Focus on election safety under network partitions, "
                "handling slow followers, and log compaction snapshots. Review difference with Paxos."
            ),
            "material_conflicts": [
                "Lecture 4 notes cite election timeouts of 150-300ms, whereas the original paper mentions that low-latency data centers may safely use 10-20ms.",
                "Section 2 implies uncommitted entries are discarded immediately on disconnect, but Section 5 clarifies they persist until overwritten.",
            ],
            "unanswered_queries": [
                "How does Raft perform cluster reconfiguration (joint consensus) when adding or removing multiple nodes simultaneously?",
                "What is the exact performance penalty of synchronous fsync disk operations for each log append in high-throughput benchmarks?",
            ],
        },
    )

    group_res.tasks.all().delete()
    GroupSessionTask.objects.bulk_create([
        GroupSessionTask(
            resource=group_res,
            title="Implement basic election timeout state machine",
            description="Write a state machine simulator in Python to demonstrate randomized timer restarts.",
            is_completed=True,
            due_date=date.today() - timedelta(days=2),
        ),
        GroupSessionTask(
            resource=group_res,
            title="Simulate 2-node network partition and verify no split-brain",
            description="Demonstrate that the partitioned minority cannot elect a leader or commit entries.",
            is_completed=True,
            due_date=date.today() - timedelta(days=1),
        ),
        GroupSessionTask(
            resource=group_res,
            title="Compare Raft Log Invariants vs Paxos Synod protocol",
            description="Prepare a 3-slide summary comparison for next group meeting.",
            is_completed=False,
            due_date=date.today() + timedelta(days=3),
        ),
        GroupSessionTask(
            resource=group_res,
            title="Review Log Compaction and Snapshotting mechanics",
            description="Understand how garbage collection of applied log entries works without stalling followers.",
            is_completed=False,
            due_date=date.today() + timedelta(days=5),
        ),
    ])

    # Conversational Q&A Thread (Chat with Material)
    doc.chat_messages.all().delete()
    StudyChatMessage.objects.bulk_create([
        StudyChatMessage(
            document=doc,
            role=StudyChatMessage.Role.USER,
            content="Can you explain the leader completeness property in simple terms?",
        ),
        StudyChatMessage(
            document=doc,
            role=StudyChatMessage.Role.ASSISTANT,
            content=(
                "Think of **Leader Completeness** as the 'knowledge inheritance' rule of Raft.\n\n"
                "If a log entry was committed in any term (meaning a majority accepted it and it became permanent), "
                "Raft guarantees that **any leader elected in future terms will ALREADY possess that exact entry in its log**.\n\n"
                "Why? Because a candidate cannot win an election without receiving votes from a majority, "
                "and at least one server in that majority must contain the committed entry. Raft's voting rule blocks "
                "any candidate whose log is less up-to-date than the voter's log!"
            ),
        ),
        StudyChatMessage(
            document=doc,
            role=StudyChatMessage.Role.USER,
            content="What happens if two candidates start an election at the exact same millisecond?",
        ),
        StudyChatMessage(
            document=doc,
            role=StudyChatMessage.Role.ASSISTANT,
            content=(
                "That leads to a **split vote**! If both candidates split the cluster's votes evenly "
                "(e.g., 2 votes to Candidate A, 2 votes to Candidate B in a 4-node cluster), neither reaches a strict majority.\n\n"
                "Here is where Raft's randomized timeouts shine: because each node picks a random timeout between 150ms and 300ms, "
                "one candidate's timer will expire before the other's. The first one to time out increments the term, starts "
                "a new election, and claims the majority votes before the other candidate even wakes up!"
            ),
        ),
    ])

    print("✓ AI Study Guide, Flashcards, Quizzes, Tasks, and Q&A Chat populated!")
    print("\n" + "=" * 60)
    print("🎉  Database Seeding Completed Successfully!")
    print("=" * 60)
    print("Login credentials for presentation:")
    print(f"  • Email:    {demo_email}")
    print(f"  • Password: {demo_password}")
    print("\nPresentation quick links (when dev server is running):")
    print("  • AI Study Assistant:  http://127.0.0.1:8000/study-assistant/")
    print("  • Material Library:    http://127.0.0.1:8000/vault/materials/")
    print(f"  • Raft Material Page:  http://127.0.0.1:8000/vault/materials/{mat_raft.id}/")
    print(f"  • Study Groups:        http://127.0.0.1:8000/groups/{group.id}/")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    seed()
