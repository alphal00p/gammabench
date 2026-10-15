use std::{env, error::Error, fs, hint::black_box, time::Instant};

fn main() -> Result<(), Box<dyn Error>> {
    let arguments: Vec<String> = env::args().skip(1).collect();
    if arguments.as_slice() == ["--version"] {
        println!("gammabench-@@BENCHMARK_ID@@ starter 1");
        return Ok(());
    }
    if !arguments.is_empty() {
        return Err("unexpected argument".into());
    }
    let items: u64 = fs::read_to_string("workload.txt")?.trim().parse()?;
    if items == 0 || items > 1_000_000 {
        return Err("starter workload must contain 1..=1000000 items".into());
    }
    let mut values: Vec<u64> = (0..items).collect();

    // Replace this starter operation together with its scope, contract and checks.
    let started = Instant::now();
    for value in black_box(&mut values) {
        *value = *value * 3 + 1;
    }
    black_box(&values);
    let compute_ns = started.elapsed().as_nanos();

    // Validate every element after the internal clock, not merely a count or hash.
    if !values.iter().copied().eq((0..items).map(|value| value * 3 + 1)) {
        return Err("transformed output differs from the exact expected sequence".into());
    }
    let total: u64 = values.iter().sum();
    // This descriptor identifies the complete sequence just checked above.
    println!("RESULT {{\"compute_ns\":{compute_ns},\"items\":{items},\"total\":{total},\"validated_output\":{{\"kind\":\"u64-affine-sequence\",\"first\":1,\"step\":3,\"length\":{items}}}}}");
    Ok(())
}
