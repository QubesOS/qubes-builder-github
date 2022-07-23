#!/bin/bash

###
### Common setup
###

# load list of qubes-builder instances
config_file="$HOME/.config/qubes-builder-github/builders.list"

# don't return anything; log it locally, just in case
mkdir -p "$HOME/builder-github-logs"
log_basename="$HOME/builder-github-logs/$(date +%s)-$$"
exec >>"${log_basename}.log" 2>&1

tmpdir=$(mktemp -d)
# setup cleanup
trap 'rm -rf $tmpdir' EXIT

###
### Common functions for qubes-builder-github scripts
###

# read clearsign-ed command from stdin, verify against keys in $1, then write
# it to the file pointed by $2.
#
# Optionally, $3 may contain a variable name where signer key fingerprint is set.
#
# The script will close stdin to be sure no other (untrusted) data is obtained.
read_stdin_command_and_verify_signature() {
    local fpr python_script_path local_keyring_path local_output_file local_signer

    if [ "$#" -ne 4 ]; then
        echo "Wrong number of arguments (expected 4, got $#)" >&2
        return 1
    fi

    python_script_path="$1"
    local_keyring_path="$2"
    local_output_file="$3"
    local_signer="$4"

    if ! [ -r "$local_keyring_path" ]; then
        echo "Keyring $local_keyring_path does not exist" >&2
        return 1
    fi

    if ! [ -d "$tmpdir" ]; then
        echo "Temporary dir $tmpdir does not exist" >&2
        return 1
    fi

    # this will read from standard input of the service, the data should be
    # considered untrusted
    "$python_script_path/lib/parse-command" "$tmpdir/untrusted_command" "$tmpdir/untrusted_command.sig" || exit
    # make sure we don't read anything else from stdin
    exec </dev/null

    if [ ! -r "$tmpdir/untrusted_command" ] || \
            [ ! -r "$tmpdir/untrusted_command.sig" ]; then
        echo "Missing parts of gpg signature" >&2
        exit 1
    fi

    if ! fpr=$(sqv --keyring "$local_keyring_path" -- \
            "$tmpdir/untrusted_command.sig" \
	    "$tmpdir/untrusted_command"); then
        echo "Invalid signature" >&2
        exit 1
    fi
    # fpr is used by eval
    # shellcheck disable=SC2034
    if ! [[ "$fpr" =~ ^[A-F0-9]{40}$ ]]; then
        echo 'Cannot obtain signing key fingerprint!' >&2
        exit 1
    fi
    # extract signer fingerprint
    if [ -n "$local_signer" ]; then
        eval "$local_signer=\$fpr"
    fi
    rm -f "$tmpdir/gpg-status"

    # now, take the first line of already verified file
    head -n 1 "$tmpdir/untrusted_command" > "$local_output_file"
    # add trailing newline back
    echo "" >> "$local_output_file"

    # log the command
    printf "Command: " >&2
    cat "$local_output_file" >&2
}


# Execute command given in $1 for each configured builder instance, in
# parallel, but only one instance will be running in given builder instance
# (the command will be called with builder.lock held).
# The command will be called with 2 arguments:
#  - release name (like r3.2)
#  - builder instance path
execute_in_each_builder() {
    local_cmd="$1"

    # look for a builder instance(s) for this release
    IFS="="
    while read -r config_release_name builder_dir; do
        if ! [ -d "$builder_dir" ]; then
            continue
        fi

        # at this point it's important to have only one instance running (and no build
        # process running), so take a lock; also go into background, as this may take
        # some time
        (
            exec 9> "$builder_dir/builder.lock"
            flock -x 9

            # avoid surprises later in the code
            IFS=' '

            # don't read $config_file (stdin for 'while' loop) and also don't mix
            # the output
            exec >>"${log_basename}-${config_release_name}.log" 2>&1 </dev/null

            $local_cmd "$config_release_name" "$builder_dir"

        ) &

    done < "$config_file"
}
