// The SDK stubs and extracted actual wrappers precede this body.
static vglMspaceStatsCache cache_snapshot(void) {
    vglMspaceStatsCache value;
    assert(vglGetMspaceStatsCache(&value, sizeof(value), VGL_MSPACE_STATS_CACHE_ABI_VERSION));
    assert(value.abi_version == 1 && value.struct_size == sizeof(value));
    assert(value.reserved[0] == 0 && value.reserved[1] == 0);
    return value;
}
static void exact_query(vglMemType type, FakeSpace *space) {
    SceClibMspaceStats stats;
    memset(&stats, 0x55, sizeof(stats));
    vgl_mspace_stats_probe_stats(type, space, &stats);
    assert(!memcmp(&stats, &space->stats, sizeof(stats)));
}
static void *fault_mutation(void *unused) {
    (void)unused; fault_thread=1; assert(vgl_malloc(13,VGL_MEM_RAM)); return NULL;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    const int verify = strstr(argv[1], "verify") != NULL || strstr(argv[1], "mismatch") != NULL;
    const int off = !strcmp(argv[1], "off");
    const uint32_t mode = off ? VGL_MSPACE_STATS_CACHE_OFF : verify ? VGL_MSPACE_STATS_CACHE_VERIFY : VGL_MSPACE_STATS_CACHE_REUSE;
    vglMspaceStatsCache poison, saved;
    memset(&poison, 0xa9, sizeof(poison)); saved = poison;
    assert(!vglGetMspaceStatsCache(&poison, sizeof(poison)-1, 1));
    assert(!memcmp(&poison, &saved, sizeof(poison)));
    assert(!vglGetMspaceStatsCache(&poison, sizeof(poison), 2));
    assert(!vglGetMspaceStatsCache(NULL, sizeof(poison), 1));
    assert(!vglSetMspaceStatsCache(3));
    assert(vglSetMspaceStatsProbe(GL_TRUE));
    assert(!vglSetMspaceStatsCache(VGL_MSPACE_STATS_CACHE_REUSE));
    assert(vglSetMspaceStatsProbe(GL_FALSE));
    assert(vglSetMspaceStatsCache(mode));
    if (!off) assert(!vglSetMspaceStatsProbe(GL_TRUE));
    assert(!cache_snapshot().initialized && cache_snapshot().mode == mode);
    const int create_failure = !strcmp(argv[1], "create-failure");
    if (create_failure) fail_create_at = 2;
    vgl_mspace_stats_probe_initialize();
    assert(!vglSetMspaceStatsCache(VGL_MSPACE_STATS_CACHE_OFF));
    if (create_failure) {
        assert(create_calls == 3 && delete_calls == 2);
        exact_query(VGL_MEM_RAM, &spaces[1]); exact_query(VGL_MEM_RAM, &spaces[1]);
        assert(raw_calls == 2);
        assert(!vglGetMspaceStatsCache(&poison, sizeof(poison), 1));
        assert(!memcmp(&poison, &saved, sizeof(poison)));
        puts("create failure: no cached result, output ABI preserved"); return 0;
    }
    exact_query(VGL_MEM_RAM, &spaces[1]); exact_query(VGL_MEM_RAM, &spaces[1]);
    if (off) {
        assert(create_calls == 0 && raw_calls == 2);
        assert(cache_snapshot().pools[1].queries == 0);
        puts("OFF: original native calls, no locks or counters"); return 0;
    }
    assert(cache_snapshot().mode == mode && cache_snapshot().initialized);
    assert(!snapshot().enabled && snapshot().pools[1].queries == 0);
    assert(raw_calls == (verify ? 2u : 1u));
    assert(cache_snapshot().pools[1].eligible_hits == 1);
    assert(cache_snapshot().pools[1].reused == (verify ? 0u : 1u));
    if (strstr(argv[1], "mutator-failure")) {
        const int waiting = strstr(argv[1], "waiting") != NULL;
        hold_query_lock = strstr(argv[1], "owned") != NULL;
        hold_query_unlock = strstr(argv[1], "unlock") != NULL;
        pthread_t query, mutation, fault;
        if (waiting) {
            block_alloc=1; assert(!pthread_create(&mutation,NULL,allocation_thread,NULL));
            pthread_mutex_lock(&barrier_mutex);
            while(!alloc_entered)pthread_cond_wait(&barrier_cond,&barrier_mutex);
            pthread_mutex_unlock(&barrier_mutex);
        }
        unsigned before=raw_calls;
        assert(!pthread_create(&query,NULL,blocked_query_thread,NULL));
        pthread_mutex_lock(&barrier_mutex);
        while(!(waiting?query_waiting:query_at_hold))pthread_cond_wait(&barrier_cond,&barrier_mutex);
        pthread_mutex_unlock(&barrier_mutex);
        assert(!pthread_create(&fault,NULL,fault_mutation,NULL));pthread_join(fault,NULL);
        pthread_mutex_lock(&barrier_mutex);release_query=release_alloc=1;pthread_cond_broadcast(&barrier_cond);pthread_mutex_unlock(&barrier_mutex);
        if(waiting)pthread_join(mutation,NULL);
        pthread_join(query,NULL);
        assert(query_result==spaces[1].stats.capacity-spaces[1].stats.current_in_use);
        assert(raw_calls==before+1);
        exact_query(VGL_MEM_RAM,&spaces[1]);assert(raw_calls==before+2);
        assert(!vglGetMspaceStatsCache(&poison,sizeof(poison),1));assert(!memcmp(&poison,&saved,sizeof(poison)));
        puts("concurrent mutex failure: current and subsequent queries native; snapshot rejected");return 0;
    }
    if (!strcmp(argv[1], "lock-failure") || !strcmp(argv[1], "unlock-failure")) {
        const unsigned before = raw_calls;
        fail_lock = !strcmp(argv[1], "lock-failure"); fail_unlock = !strcmp(argv[1], "unlock-failure");
        exact_query(VGL_MEM_RAM, &spaces[1]); exact_query(VGL_MEM_RAM, &spaces[1]);
        assert(raw_calls == before + 2);
        assert(!vglGetMspaceStatsCache(&poison, sizeof(poison), 1));
        assert(!memcmp(&poison, &saved, sizeof(poison)));
        puts("mutex failure: cached answer not returned, future queries native"); return 0;
    }
    if (strstr(argv[1], "mismatch")) {
        const unsigned field = (unsigned)(argv[1][strlen(argv[1])-1]-'0');
        assert(field < 4);
        uint32_t *fields[] = {&spaces[1].stats.capacity, &spaces[1].stats.unk,
                            &spaces[1].stats.peak_in_use, &spaces[1].stats.current_in_use};
        ++*fields[field];
        if (field == 0) spaces[1].stats.unk = spaces[1].stats.capacity;
        exact_query(VGL_MEM_RAM, &spaces[1]);
        vglMspaceStatsCache value = cache_snapshot();
        assert(value.validation_failed);
        assert(value.pools[1].mismatches == (field == 1 ? 0u : 1u));
        assert(value.pools[1].invalid_queries == (field == 1 ? 1u : 0u));
        const unsigned before = raw_calls;
        exact_query(VGL_MEM_VRAM, &spaces[0]); exact_query(VGL_MEM_VRAM, &spaces[0]);
        assert(raw_calls == before + 2 && cache_snapshot().pools[0].eligible_hits == 0);
        puts("verify difference: native result retained, sticky global reuse rejection"); return 0;
    }
    if (strstr(argv[1], "incomplete")) {
        GLboolean locked = vgl_mspace_stats_probe_begin(VGL_MEM_RAM);
        assert(locked); vgl_mspace_stats_probe_end(VGL_MEM_RAM, locked);
        incomplete_stats = (unsigned)(argv[1][strlen(argv[1])-1]-'0');
        (void)vgl_mem_get_free_space(VGL_MEM_RAM);
        assert(cache_snapshot().validation_failed && cache_snapshot().pools[1].invalid_queries == 1);
        incomplete_stats = 0;
        unsigned before = raw_calls; exact_query(VGL_MEM_RAM, &spaces[1]); exact_query(VGL_MEM_RAM, &spaces[1]);
        assert(raw_calls == before + 2);
        puts("incomplete API: no saved result admitted after partial/absent output"); return 0;
    }
    for (unsigned failed = 0; failed < 2; ++failed) {
        fail_allocation = failed;
        for (unsigned op = 0; op < 5; ++op) {
            vglMspaceStatsCache a = cache_snapshot();
            switch (op) {
            case 0: (void)vgl_malloc(3, VGL_MEM_RAM); break;
            case 1: (void)vgl_calloc(2, 2, VGL_MEM_RAM); break;
            case 2: (void)vgl_memalign(16, 5, VGL_MEM_RAM); break;
            case 3: (void)vgl_realloc(&spaces[1], 6); break;
            case 4: vgl_free(&spaces[1]); break;
            }
            query_pair();
            vglMspaceStatsCache b = cache_snapshot();
            assert(b.pools[1].mutation_attempts == a.pools[1].mutation_attempts + 1);
            assert(b.pools[1].eligible_hits == a.pools[1].eligible_hits + 1);
            assert(b.pools[1].native_calls == a.pools[1].native_calls + (verify ? 2 : 1));
            assert(b.pools[1].reused == a.pools[1].reused + (verify ? 0 : 1));
            assert(!b.validation_failed);
        }
    }
    fail_allocation = 0;
    FakeSpace replacement = {{2100,2100,7,7}};
    unsigned before = raw_calls;
    exact_query(VGL_MEM_RAM, &replacement); exact_query(VGL_MEM_RAM, &spaces[1]);
    assert(raw_calls == before + 2);
    before = raw_calls;
    mempool_size[VGL_MEM_BUDGET] = 0;
    assert(vgl_mem_get_free_space(VGL_MEM_BUDGET) == 0 && vgl_mem_get_free_space(VGL_MEM_EXTERNAL) == 0);
    assert(raw_calls == before); mempool_size[VGL_MEM_BUDGET] = 4000;
    size_t total = 0; for (unsigned i = 0; i < 4; ++i) total += spaces[i].stats.capacity - spaces[i].stats.current_in_use;
    assert(vgl_mem_get_free_space(VGL_MEM_ALL) == total);
    vgl_mspace_stats_probe_initialize(); assert(create_calls == 4);
    before = raw_calls; assert(vgl_mem_get_free_space(VGL_MEM_ALL) == total); assert(raw_calls == before + 4);
    assert(!vgl_mspace_stats_probe_begin_impl((vglMemType)-1));
    assert(!vgl_mspace_stats_probe_begin_impl(VGL_MEM_EXTERNAL));
    block_alloc = 1;
    pthread_t allocation, query, stress[3];
    assert(!pthread_create(&allocation, NULL, allocation_thread, NULL));
    pthread_mutex_lock(&barrier_mutex);
    while (!alloc_entered) pthread_cond_wait(&barrier_cond, &barrier_mutex);
    pthread_mutex_unlock(&barrier_mutex);
    assert(!pthread_create(&query, NULL, blocked_query_thread, NULL));
    pthread_mutex_lock(&barrier_mutex);
    while (!query_waiting) pthread_cond_wait(&barrier_cond, &barrier_mutex);
    assert(!query_done); (void)vgl_mem_get_free_space(VGL_MEM_VRAM);
    release_alloc = 1; pthread_cond_broadcast(&barrier_cond); pthread_mutex_unlock(&barrier_mutex);
    pthread_join(allocation, NULL); pthread_join(query, NULL); block_alloc = 0;
    assert(query_done); exact_query(VGL_MEM_RAM, &spaces[1]);
    for (unsigned i = 0; i < 3; ++i) assert(!pthread_create(&stress[i], NULL, stress_thread, NULL));
    for (unsigned i = 0; i < 3; ++i) pthread_join(stress[i], NULL);
    vglMspaceStatsCache value = cache_snapshot(); assert(!value.validation_failed);
    for (unsigned i = 0; i < 4; ++i) {
        assert(value.pools[i].queries == value.pools[i].native_calls + value.pools[i].reused);
        assert(value.pools[i].eligible_hits <= value.pools[i].queries);
        assert(value.pools[i].verification_checks == (verify ? value.pools[i].eligible_hits : 0));
    }
    puts("actual wrappers: exact results, all mutators/failures, identity, lifecycle and concurrency pass");
    return 0;
}
