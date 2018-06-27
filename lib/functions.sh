#!/bin/sh

###
### Common setup
###

# load list of qubes-builder instances
config_file="$HOME/.config/qubes-builder-github/builders.list"

# don't return anything; log it locally, just in case
mkdir -p $HOME/builder-github-logs
log_basename="$HOME/builder-github-logs/$(date +%s)-$$"
exec >>${log_basename}.log 2>&1

tmpdir=$(mktemp -d)
# setup cleanup
trap "rm -rf $tmpdir" EXIT

###
### Common functions for qubes-builder-github scripts
###

# read clearsign-ed command from stdin, verify against keys in $1, then write
# it to the file pointed by $2.
# The script will close stdin to be sure no other (untrusted) data is obtained.
read_stdin_command_and_verify_signature() {
    local keyring_path="$1"
    local output_file="$2"

    if ! [ -r "$keyring_path" ]; then
        echo "Keyring $keyring_path does not exist" >&2
        return 1
    fi

    if ! [ -d "$tmpdir" ]; then
        echo "Temporary dir $tmpdir does not exist" >&2
        return 1
    fi

    # this will read from standard input of the service, the data should be
    # considered untrusted
    tr -d '\r' | awk -b \
            -v in_command=0 \
            -v in_signature=0 \
            -v output_data="$tmpdir/untrusted_command.tmp" \
            -v output_sig="$tmpdir/untrusted_command.sig" \
            '
        /^-----BEGIN PGP SIGNED MESSAGE-----$/ {
            # skip first 3 lines (this one, hash declaration and empty line)
            in_command=4
        }
        /^-----BEGIN PGP SIGNATURE-----$/ {
            in_command=0
            in_signature=1
        }
        {
            if (in_command > 1) {
                in_command--
                next
            }
        }
        {
            if (in_command) print >output_data
            if (in_signature) print >output_sig
        }
        /^-----END PGP SIGNATURE-----$/ {
            in_signature=0
        }
    '

    # make sure we don't read anything else from stdin
    exec </dev/null

    if [ ! -r "$tmpdir/untrusted_command.tmp" -o \
         ! -r "$tmpdir/untrusted_command.sig" ]; then
        echo "Missing parts of gpg signature" >&2
        exit 1
    fi

    # gpg --clearsign apparently ignore trailing newline while calculating hash. So
    # must do the same here for signature verification. This is stupid.
    head -c -1 "$tmpdir/untrusted_command.tmp" > "$tmpdir/untrusted_command"

    if ! gpgv2 --keyring "$keyring_path" \
            "$tmpdir/untrusted_command.sig" \
            "$tmpdir/untrusted_command"; then
        echo "Invalid signature" >&2
        exit 1
    fi

    # now, take the first line of already verified file
    head -n 1 "$tmpdir/untrusted_command" > "$output_file"
    # add trailing newline back
    echo "" >> "$output_file"

    # log the command
    echo -n "Command: " >&2
    cat "$output_file" >&2
}


# Execute command given in $1 for each configured builder instance, in
# parallel, but only one instance will be running in given builder instance
# (the command will be called with builder.lock held).
# The command will be called with 2 arguments:
#  - release name (like r3.2)
#  - builder instance path
execute_in_each_builder() {
    local cmd="$1"

    # look for a builder instance(s) for this release
    IFS="="
    while read config_release_name builder_dir; do
        if ! [ -d "$builder_dir" ]; then
            continue
        fi

        # at this point it's important to have only one instance running (and no build
        # process running), so take a lock; also go into background, as this may take
        # some time
        (
            exec 9> "$builder_dir/builder.lock"
            flock -x 9

            # don't read $config_file (stdin for 'while' loop) and also don't mix
            # the output
            exec >>"${log_basename}-${config_release_name}.log" 2>&1 </dev/null

            $cmd "$config_release_name" "$builder_dir"

        ) &

    done < "$config_file"
}
