#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>

enum { CHUNKS = 4, CHUNK = 32 << 20, HUGE = 2 << 20 };

static void check(int ok, const char *what) {
    if (!ok) {
        perror(what);
        exit(EXIT_FAILURE);
    }
}

static void *map_huge_aligned(void) {
    char *mapping = mmap(NULL, CHUNK + HUGE, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    check(mapping != MAP_FAILED, "mmap");
    size_t prefix = (-(uintptr_t)mapping) & (HUGE - 1);
    char *buffer = mapping + prefix;
    if (prefix)
        check(munmap(mapping, prefix) == 0, "munmap prefix");
    check(munmap(buffer + CHUNK, HUGE - prefix) == 0, "munmap suffix");
    check(madvise(buffer, CHUNK, MADV_HUGEPAGE) == 0, "MADV_HUGEPAGE");
    return buffer;
}

struct stats {
    unsigned long vma_kb, covered_kb, rss_kb, anon_kb, huge_kb, eligible_vmas;
    unsigned int vmas;
};

static struct stats read_smaps(void *const *buffers) {
    FILE *file = fopen("/proc/self/smaps", "r");
    check(file != NULL, "fopen smaps");
    struct stats stats = {0};
    char line[1024];
    unsigned long start, end, value;
    int selected = 0;
    while (fgets(line, sizeof(line), file)) {
        if (sscanf(line, "%lx-%lx", &start, &end) == 2) {
            size_t covered = 0;
            for (size_t i = 0; i < CHUNKS; ++i) {
                uintptr_t p = (uintptr_t)buffers[i];
                uintptr_t left = p > start ? p : start;
                uintptr_t right = p + CHUNK < end ? p + CHUNK : end;
                if (left < right)
                    covered += right - left;
            }
            selected = covered > 0;
            if (selected) {
                stats.vmas++;
                stats.vma_kb += (end - start) / 1024;
                stats.covered_kb += covered / 1024;
            }
        } else if (selected) {
            if (sscanf(line, "Rss: %lu", &value) == 1)
                stats.rss_kb += value;
            else if (sscanf(line, "Anonymous: %lu", &value) == 1)
                stats.anon_kb += value;
            else if (sscanf(line, "AnonHugePages: %lu", &value) == 1)
                stats.huge_kb += value;
            else if (sscanf(line, "THPeligible: %lu", &value) == 1)
                stats.eligible_vmas += value;
        }
    }
    check(!ferror(file), "read smaps");
    check(fclose(file) == 0, "fclose smaps");
    if (stats.covered_kb != CHUNKS * CHUNK / 1024) {
        errno = EIO;
        check(0, "smaps did not cover all backing allocations");
    }
    return stats;
}

static void print_stats(struct stats stats) {
    printf("{\"vmas\":%u,\"vma_kb\":%lu,\"covered_kb\":%lu,"
           "\"rss_kb\":%lu,\"anonymous_kb\":%lu,\"anon_huge_kb\":%lu,"
           "\"thp_eligible_vmas\":%lu}", stats.vmas, stats.vma_kb,
           stats.covered_kb, stats.rss_kb, stats.anon_kb, stats.huge_kb,
           stats.eligible_vmas);
}

int main(int argc, char **argv) {
    if (argc != 2 || (strcmp(argv[1], "current") && strcmp(argv[1], "thp"))) {
        fprintf(stderr, "Usage: %s current|thp\n", argv[0]);
        return 2;
    }
    int use_thp = strcmp(argv[1], "thp") == 0;
    long page = sysconf(_SC_PAGESIZE);
    check(page == 4096, "expected 4 KiB base pages");
    struct utsname host;
    check(uname(&host) == 0, "uname");

    void *buffers[CHUNKS];
    size_t huge_aligned = 0;
    for (size_t i = 0; i < CHUNKS; ++i) {
        buffers[i] = use_thp ? map_huge_aligned() : aligned_alloc(4096, CHUNK);
        check(buffers[i] != NULL, "aligned_alloc");
        huge_aligned += (uintptr_t)buffers[i] % HUGE == 0;
    }
    struct stats before = read_smaps(buffers);
    struct rusage first, last;
    struct timespec start, end;
    check(getrusage(RUSAGE_SELF, &first) == 0, "getrusage before touch");
    check(clock_gettime(CLOCK_MONOTONIC, &start) == 0, "clock_gettime before touch");
    for (size_t i = 0; i < CHUNKS; ++i) {
        volatile unsigned char *data = buffers[i];
        for (size_t offset = 0; offset < CHUNK; offset += (size_t)page)
            data[offset] = 1;
    }
    check(clock_gettime(CLOCK_MONOTONIC, &end) == 0, "clock_gettime after touch");
    check(getrusage(RUSAGE_SELF, &last) == 0, "getrusage after touch");
    struct stats after = read_smaps(buffers);
    double touch_ms = (end.tv_sec - start.tv_sec) * 1000.0 +
                      (end.tv_nsec - start.tv_nsec) / 1000000.0;

    printf("{\"node\":\"%s\",\"kernel\":\"%s\",\"mode\":\"%s\","
           "\"chunk_bytes\":%u,\"chunks\":%u,\"base_page_bytes\":%ld,"
           "\"huge_aligned_chunks\":%zu,\"touch_ms\":%.3f,"
           "\"touch_minor_faults\":%ld,\"touch_major_faults\":%ld,\"before\":",
           host.nodename, host.release, argv[1], CHUNK, CHUNKS, page, huge_aligned,
           touch_ms, last.ru_minflt - first.ru_minflt, last.ru_majflt - first.ru_majflt);
    print_stats(before);
    printf(",\"after\":");
    print_stats(after);
    puts("}");

    for (size_t i = 0; i < CHUNKS; ++i) {
        if (use_thp)
            check(munmap(buffers[i], CHUNK) == 0, "munmap backing");
        else
            free(buffers[i]);
    }
    return 0;
}
