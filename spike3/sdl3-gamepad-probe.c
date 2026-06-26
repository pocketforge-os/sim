// sdl3-gamepad-probe.c — SPIKE-3 load-bearing probe (tsp-an4.2).
//
// Enumerates SDL3 joysticks/gamepads and dumps, as deterministic JSON, exactly the
// facts that decide whether a host-synthesized `uinput` device is INDISTINGUISHABLE
// from real TrimUI hardware to SDL3 gamepad enumeration:
//   - joystick name / vendor / product / version
//   - the SDL joystick GUID (raw 32-hex AND the gamecontrollerdb form with the
//     name-CRC field, bytes 2-3, zeroed — that is the form `pf caps emit-sdldb` ships)
//   - SDL_IsGamepad (did SDL match a gamepad mapping at all)
//   - the mapping string SDL resolved (SDL_GetGamepadMapping)
//   - the resolved button/axis bindings (SDL_GetGamepadBindings) — ground truth,
//     independent of mapping-string formatting
//
// The SAME binary is run native-x86 and arm64-under-qemu-tsp; the two JSON blobs must
// be byte-identical (the indistinguishability claim). It is HONEST ABOUT INPUT ONLY:
// SDL is built with no video/audio/udev (SDL_VIDEODRIVER=dummy if video is present),
// so NO GPU/PowerVR blob is touched — rendering is a separate bead (tsp-an4.4).
//
// Build: see ../sdl3/build-sdl3.sh (static, gamepad-only). Run: ./run-spike3.sh.
//
// Optional env honored by SDL itself: SDL_GAMECONTROLLERCONFIG=<one mapping line>
// forces SDL to use that mapping for the matching GUID — the run script feeds it the
// descriptor's `pf caps emit-sdldb a133` line to prove the one-descriptor->SDL path.

#include <SDL3/SDL.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static void guid_to_hex(SDL_GUID g, char out[33]) {
    static const char *h = "0123456789abcdef";
    for (int i = 0; i < 16; i++) { out[i*2] = h[g.data[i] >> 4]; out[i*2+1] = h[g.data[i] & 0xf]; }
    out[32] = 0;
}

// JSON string escaper (enough for device names / mapping strings).
static void jstr(const char *s) {
    putchar('"');
    for (; s && *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') { putchar('\\'); putchar(c); }
        else if (c == '\n') { fputs("\\n", stdout); }
        else if (c < 0x20) { printf("\\u%04x", c); }
        else putchar(c);
    }
    putchar('"');
}

static const char *axis_name(SDL_GamepadAxis a) {
    const char *n = SDL_GetGamepadStringForAxis(a);
    return n ? n : "?";
}
static const char *button_name(SDL_GamepadButton b) {
    const char *n = SDL_GetGamepadStringForButton(b);
    return n ? n : "?";
}

int main(int argc, char **argv) {
    // Match by VID/PID, NOT name: once SDL applies its built-in 045e:028e mapping it
    // RENAMES the joystick to the mapping's name ("Xbox 360 Controller") — the raw
    // EVIOCGNAME ("TRIMUI Player1") survives only as the crc16 in the GUID's bytes 2-3.
    // The real TrimUI hardware is renamed identically, so vid/pid is the stable identity.
    unsigned want_vid = (argc > 1) ? (unsigned)strtoul(argv[1], NULL, 16) : 0x045e;
    unsigned want_pid = (argc > 2) ? (unsigned)strtoul(argv[2], NULL, 16) : 0x028e;

    if (!SDL_Init(SDL_INIT_GAMEPAD)) {
        printf("{\"error\":\"SDL_Init failed: ");
        // crude escape of SDL error onto one line
        const char *e = SDL_GetError();
        for (; e && *e; e++) if (*e != '"' && *e != '\\' && *e >= 0x20) putchar(*e);
        printf("\"}\n");
        return 2;
    }

    int jcount = 0;
    SDL_JoystickID *js = SDL_GetJoysticks(&jcount);

    // Find the joystick by vid/pid (deterministic across runs/arch). Count matches so a
    // stale duplicate is visible rather than silently picked.
    SDL_JoystickID target = 0;
    int nmatch = 0;
    for (int i = 0; i < jcount; i++) {
        if (SDL_GetJoystickVendorForID(js[i]) == want_vid &&
            SDL_GetJoystickProductForID(js[i]) == want_pid) {
            if (!target) target = js[i];
            nmatch++;
        }
    }

    printf("{\n");
    printf("  \"sdl_version\": \"%d.%d.%d\",\n",
           SDL_MAJOR_VERSION, SDL_MINOR_VERSION, SDL_MICRO_VERSION);
    printf("  \"match_vid\": \"%04x\",\n", want_vid);
    printf("  \"match_pid\": \"%04x\",\n", want_pid);
    printf("  \"joystick_count\": %d,\n", jcount);
    printf("  \"vidpid_matches\": %d,\n", nmatch);
    printf("  \"found\": %s", target ? "true" : "false");

    if (target) {
        const char *nm = SDL_GetJoystickNameForID(target);
        Uint16 ven = SDL_GetJoystickVendorForID(target);
        Uint16 prod = SDL_GetJoystickProductForID(target);
        Uint16 ver = SDL_GetJoystickProductVersionForID(target);
        SDL_GUID g = SDL_GetJoystickGUIDForID(target);
        char raw[33]; guid_to_hex(g, raw);
        SDL_GUID gm = g; gm.data[2] = 0; gm.data[3] = 0;   // zero the name-CRC field
        char masked[33]; guid_to_hex(gm, masked);
        bool isgp = SDL_IsGamepad(target);

        printf(",\n  \"joystick\": {\n");
        printf("    \"name\": "); jstr(nm); printf(",\n");
        printf("    \"vendor\": \"%04x\",\n", ven);
        printf("    \"product\": \"%04x\",\n", prod);
        printf("    \"version\": \"%04x\",\n", ver);
        printf("    \"guid_raw\": \"%s\",\n", raw);
        printf("    \"guid_gamecontrollerdb\": \"%s\"\n", masked);
        printf("  },\n");
        printf("  \"is_gamepad\": %s", isgp ? "true" : "false");

        if (isgp) {
            SDL_Gamepad *gp = SDL_OpenGamepad(target);
            printf(",\n  \"gamepad\": {\n");
            char *map = gp ? SDL_GetGamepadMapping(gp) : NULL;
            printf("    \"mapping\": "); jstr(map ? map : ""); printf(",\n");
            if (map) SDL_free(map);

            // Resolved bindings = the ground truth of what SDL bound, regardless of
            // how the mapping string is spelled. Emit as a sorted-ish list of
            // {output -> source} so native and qemu-tsp produce identical text.
            int bc = 0;
            SDL_GamepadBinding **b = gp ? SDL_GetGamepadBindings(gp, &bc) : NULL;
            printf("    \"bindings\": [");
            for (int i = 0; i < bc; i++) {
                SDL_GamepadBinding *bd = b[i];
                // output element
                char outbuf[48];
                if (bd->output_type == SDL_GAMEPAD_BINDTYPE_BUTTON)
                    snprintf(outbuf, sizeof outbuf, "%s", button_name(bd->output.button));
                else if (bd->output_type == SDL_GAMEPAD_BINDTYPE_AXIS)
                    snprintf(outbuf, sizeof outbuf, "%s", axis_name(bd->output.axis.axis));
                else snprintf(outbuf, sizeof outbuf, "none");
                // input source
                char inbuf[48];
                if (bd->input_type == SDL_GAMEPAD_BINDTYPE_BUTTON)
                    snprintf(inbuf, sizeof inbuf, "b%d", bd->input.button);
                else if (bd->input_type == SDL_GAMEPAD_BINDTYPE_AXIS)
                    snprintf(inbuf, sizeof inbuf, "a%d", bd->input.axis.axis);
                else if (bd->input_type == SDL_GAMEPAD_BINDTYPE_HAT)
                    snprintf(inbuf, sizeof inbuf, "h%d.%d", bd->input.hat.hat, bd->input.hat.hat_mask);
                else snprintf(inbuf, sizeof inbuf, "none");
                printf("%s\n      {\"out\": \"%s\", \"in\": \"%s\"}", i ? "," : "", outbuf, inbuf);
            }
            if (b) SDL_free(b);
            printf("\n    ]\n  }");
            if (gp) SDL_CloseGamepad(gp);
        }
    }
    printf("\n}\n");

    if (js) SDL_free(js);
    SDL_Quit();
    return target ? 0 : 1;
}
